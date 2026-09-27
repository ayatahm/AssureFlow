"""Completion guards, neutral outcome and atomic report issue (spec 15, 18)."""
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.assurance.models import AcceptanceState, Action, ActionState, ConclusionStatus
from apps.audit.services import record
from apps.core.errors import ServiceError
from apps.reviews.models import Outcome, Review, ReviewState
from apps.reviews.services.common import require, transition

from . import status as st


@dataclass
class CompletionCheck:
    blockers: list = field(default_factory=list)
    checklist: list = field(default_factory=list)  # (label, ok)
    final_risk: object = None
    outcome: str = ""
    unresolved: list = field(default_factory=list)  # (target, treatment status, acceptance)

    @property
    def ok(self):
        return not self.blockers


def evaluate_completion(review, today=None):
    today = today or timezone.localdate()
    chk = CompletionCheck()

    def gate(label, problems):
        chk.checklist.append((label, not problems))
        chk.blockers.extend(problems)

    # 2. every presented question answered
    unanswered = list(review.questions.filter(answer="").values_list("question__code", flat=True))
    gate("All presented questions answered", [f"Question {c} is unanswered." for c in unanswered])

    # 3/4. every applicable requirement has an explicit current conclusion, none Evidence Required
    missing, evidence_required = [], []
    for rr in st.applicable_requirements(review):
        current, latest = st.conclusion_state(rr)
        if current is None:
            if latest is not None:
                missing.append(f"{rr.control.code}: answer changed after the conclusion; re-review required.")
            else:
                missing.append(f"{rr.control.code} has no Reviewer conclusion.")
        elif current.status == ConclusionStatus.EVIDENCE_REQUIRED:
            evidence_required.append(f"{rr.control.code} is still Evidence Required.")
    gate("All applicable controls concluded", missing)
    gate("No Evidence Required conclusion remains", evidence_required)

    # 5. technical scope explicitly confirmed
    gate("Technical scope confirmed", [] if review.scope_confirmed_at else ["Technical scope has not been confirmed."])

    # 6. authoritative risk record
    chk.final_risk = st.latest_risk(review)
    gate("Risk judgement recorded", [] if chk.final_risk else ["No risk judgement has been recorded."])

    # 7. no submitted actions / pending acceptances
    submitted = Action.objects.filter(finding__review=review, state=ActionState.SUBMITTED)
    pending = review.acceptances.filter(state=AcceptanceState.PENDING)
    gate("No Submitted action or Pending acceptance",
         [f"Action {a.code} is Submitted and awaits verification." for a in submitted]
         + [f"Acceptance {a.code} is Pending." for a in pending])

    # 8/9/11. every remaining gap / unresolved technical finding has a current decided treatment
    treatment_problems = []
    for rr in st.requirement_gaps(review):
        if st.current_compliance(rr) is None:
            treatment_problems.append(f"{rr.control.code}: no Project Compliance Decision recorded.")
        status, acc = st.target_treatment(review, rr, chk.final_risk, today)
        chk.unresolved.append((rr, status, acc))
        if status not in (st.ACCEPTED, st.DECLINED):
            treatment_problems.append(f"{rr.control.code}: {st.TREATMENT_LABELS[status]}.")
    for item in st.unresolved_technical_items(review):
        status, acc = st.target_treatment(review, item, chk.final_risk, today)
        chk.unresolved.append((item, status, acc))
        if status not in (st.ACCEPTED, st.DECLINED):
            treatment_problems.append(
                f"Technical finding on {item.control.source_id}: no verified remediation and "
                f"{st.TREATMENT_LABELS[status].lower()}."
            )
    gate("Every remaining gap and technical finding has a decided treatment", treatment_problems)

    # 10. every OPEN action is covered by its finding's alternative treatment
    open_problems = []
    for action in Action.objects.filter(finding__review=review, state=ActionState.OPEN).select_related("finding"):
        status, _ = st.target_treatment(review, action.finding.target, chk.final_risk, today)
        if status not in (st.ACCEPTED, st.DECLINED):
            open_problems.append(f"Action {action.code} is Open with no alternative treatment.")
    gate("Every Open action is remediated or explicitly treated", open_problems)

    if chk.ok:
        statuses = [s for _, s, _ in chk.unresolved]
        if not statuses:
            chk.outcome = Outcome.MET
        elif any(s == st.DECLINED for s in statuses):
            chk.outcome = Outcome.NOT_MET
        else:
            chk.outcome = Outcome.MET_WITH_EXCEPTIONS
    return chk


@transaction.atomic
def complete_review(actor, review, *, final_summary, conditions, request=None):
    from apps.reporting.services import issue_snapshot

    review = Review.objects.select_for_update().get(pk=review.pk)
    require(actor, "review.complete", review)
    if not (final_summary or "").strip():
        raise ServiceError("A final summary is required.")
    if not (conditions or "").strip():
        raise ServiceError("Record conditions, or the literal 'None'.")
    chk = evaluate_completion(review)
    if not chk.ok:
        raise ServiceError("The review cannot be completed yet.", chk.blockers)

    now = timezone.now()
    review.final_summary = final_summary.strip()
    review.conditions = conditions.strip()
    review.final_risk_record = chk.final_risk
    review.outcome = chk.outcome
    review.completed_at = now
    review.save()
    transition(actor, review, ReviewState.COMPLETED, request=request, detail={"outcome": chk.outcome})
    snapshot = issue_snapshot(actor, review, chk, issued_at=now)
    record("report.issued", actor=actor, review=review, obj=snapshot, request=request,
           detail={"revision": snapshot.revision, "sha256": snapshot.sha256,
                   "serializer_version": snapshot.serializer_version, "outcome": chk.outcome,
                   "final_risk_record": chk.final_risk.pk})
    return snapshot
