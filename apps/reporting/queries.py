"""Read-side queries: live scope summary, Assurance Trace and question explanations."""
from apps.assurance.models import EvidenceLink
from apps.assurance.services import status as st
from apps.reviews.models import Applicability, ParticipantRole, RequirementLevel


def scope_summary(review):
    """Generated live from ReviewRequirement / ReviewQuestion rows — never stored (spec 17)."""
    reqs = review.requirements.all()
    total_questions = review.rule_set.assessment_questions.count() if review.rule_set_id else 0
    asked = review.questions.count()
    return {
        "evaluated": reqs.count(),
        "applicable": reqs.filter(applicability=Applicability.KEEP).count(),
        "not_applicable": reqs.filter(applicability=Applicability.SKIP).count(),
        "required": reqs.filter(applicability=Applicability.KEEP, level=RequirementLevel.REQUIRED).count(),
        "recommended": reqs.filter(applicability=Applicability.KEEP, level=RequirementLevel.RECOMMENDED).count(),
        "optional": reqs.filter(applicability=Applicability.KEEP, level=RequirementLevel.OPTIONAL).count(),
        "questions_asked": asked,
        "questions_removed": max(0, total_questions - asked),
    }


def question_explanation(review_question):
    """Lightweight 'Why am I being asked this?' projection (spec 16.1)."""
    review = review_question.review
    linked = review.requirements.filter(
        control__questions=review_question.question, applicability=Applicability.KEEP
    ).select_related("control")
    return {
        "question": review_question.question,
        "selection_reason": review_question.selection_reason,
        "always_ask": review_question.question.always_ask,
        "rule_version": review.rule_set.version if review.rule_set_id else "",
        "requirements": [
            {"code": rr.control.code, "title": rr.control.title, "level": rr.get_level_display(),
             "reason": rr.reason}
            for rr in linked
        ],
    }


def build_trace(review, rr, *, viewer_role):
    """Assurance Trace for one requirement, built from existing records (spec 16).

    ``viewer_role`` selects the field projection:
    * REVIEWER — full record including history and framework references;
    * REQUESTER — recorded conclusions/findings/treatments, no internal history;
    * BUSINESS_OWNER — covered item only (caller has already checked coverage).
    """
    full = viewer_role == ParticipantRole.REVIEWER
    questions = list(
        review.questions.filter(question__in=rr.control.questions.all())
        .select_related("question", "answered_by")
        .order_by("question__order")
    )
    flags = list(rr.flags.select_related("source_question__question").order_by("created_at")) if full else []
    current, latest = st.conclusion_state(rr)
    history = list(rr.conclusions.select_related("decided_by").order_by("-decided_at", "-pk")) if full else []

    evidence = list(
        EvidenceLink.objects.filter(review_requirement=rr).select_related("evidence__uploaded_by").order_by("evidence_id")
    )
    findings = []
    for f in rr.findings.prefetch_related("actions__history", "actions__evidence_links__evidence").order_by("pk"):
        actions = []
        for a in f.actions.all().order_by("pk"):
            actions.append({
                "action": a,
                "history": list(a.history.all().order_by("at", "pk")),
                "evidence": list(a.evidence_links.select_related("evidence__uploaded_by").order_by("evidence_id")),
            })
        findings.append({"finding": f, "actions": actions})

    compliance_current = st.current_compliance(rr)
    compliance_history = list(rr.compliance_decisions.select_related("decided_by").order_by("-decided_at")) if full else []

    final_risk = review.final_risk_record or st.latest_risk(review)
    treatment_status, acceptance = st.target_treatment(review, rr, final_risk)
    acceptances = [
        item.acceptance for item in rr.acceptance_items.select_related("acceptance__business_owner",
                                                                         "acceptance__risk_record")
        .order_by("-acceptance__created_at")
    ]

    report_entry = None
    snapshot = review.snapshots.order_by("revision").first()
    if snapshot:
        for entry in snapshot.data.get("requirements", []):
            if entry["id"] == rr.pk:
                report_entry = entry
                break

    return {
        "rr": rr,
        "full": full,
        "framework_refs": list(rr.control.framework_refs.all()) if full else [],
        "questions": questions,
        "flags": flags,
        "evidence": evidence,
        "conclusion": current,
        "conclusion_stale": current is None and latest is not None,
        "stale_conclusion": latest if current is None else None,
        "conclusion_history": history,
        "findings": findings,
        "compliance": compliance_current,
        "compliance_history": compliance_history,
        "risk": final_risk,
        "risk_pinned": bool(review.final_risk_record_id),
        "treatment_status": treatment_status,
        "treatment_label": st.TREATMENT_LABELS.get(treatment_status, ""),
        "treatment_acceptance": acceptance,
        "acceptances": acceptances,
        "report_entry": report_entry,
        "snapshot": snapshot,
    }
