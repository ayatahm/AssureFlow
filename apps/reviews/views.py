from itertools import groupby

from django.contrib import messages
from django.core.exceptions import PermissionDenied as HttpForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.assurance.models import ConclusionStatus, EvidenceLink
from apps.assurance.services import status as st
from apps.audit.models import AuditEvent
from apps.content.models import TechnicalControl
from apps.core.permissions import can, can_create_review, visible_reviews
from apps.core.viewhelpers import load_review, review_context, run
from apps.reporting.queries import question_explanation, scope_summary
from apps.reviews.models import (
    Answer,
    Applicability,
    ReviewQuestion,
    ReviewRequirement,
    ReviewState,
    ReviewTechnicalItem,
)

from .forms import (
    AddTechnicalItemForm,
    AnswerForm,
    IntakeForm,
    ReasonForm,
    ScopeConfirmForm,
    TechnicalItemForm,
    TriageForm,
)
from .services import assessment, generation, intake, technical, triage


def review_list(request):
    reviews = visible_reviews(request.user).select_related("department")
    state = request.GET.get("state")
    if state in ReviewState.values:
        reviews = reviews.filter(state=state)
    return render(request, "reviews/list.html", {
        "reviews": reviews.order_by("-updated_at"), "states": ReviewState.choices, "state": state,
        "can_create": can_create_review(request.user),
    })


def review_new(request):
    if not can_create_review(request.user):
        raise HttpForbidden("review.create")
    form = IntakeForm(request.POST or None, actor=request.user, creating=True)
    if request.method == "POST" and form.is_valid():
        data = {k: form.cleaned_data[k] for k in intake.INTAKE_FIELDS if k in form.cleaned_data}
        review, ok = run(
            request, intake.create_review, request.user, data,
            reviewer=form.cleaned_data["assigned_reviewer"], requester=form.cleaned_data.get("requester"),
            scope_tags=form.cleaned_data["scope_tags"], success="Security Review saved as Draft.",
        )
        if ok:
            if "continue" in request.POST:
                return redirect("reviews:triage", pk=review.pk)
            return redirect("reviews:detail", pk=review.pk)
    return render(request, "reviews/intake_form.html", {"form": form, "creating": True, "step": 1})


def review_detail(request, pk):
    review = load_review(request, pk)
    summary = scope_summary(review) if review.generated_at else None
    questions = review.questions.all()
    open_findings = review.findings.exclude(actions__state="CLOSED").distinct().count() if review.pk else 0
    return render(request, "reviews/detail.html", review_context(
        request, review, "overview",
        summary=summary,
        answered=questions.exclude(answer="").count(),
        total_questions=questions.count(),
        open_findings=open_findings,
        scope_tags=review.scope_tags.select_related("scope_tag"),
        snapshot=review.snapshots.first(),
        routing_on=_routing_labels(review),
    ))


def _routing_labels(review):
    labels = {r["code"]: r["label"] for r in (review.rule_set.routing_config if review.rule_set_id else [])}
    return [labels.get(code, code) for code, on in sorted(review.routing.items()) if on]


def intake_edit(request, pk):
    review = load_review(request, pk, "review.edit_intake")
    form = IntakeForm(request.POST or None, instance=review, actor=request.user, creating=False)
    if request.method == "POST" and form.is_valid():
        data = {k: form.cleaned_data[k] for k in intake.INTAKE_FIELDS if k in form.cleaned_data}
        # Re-read so unsaved form mutations on the instance never bypass the service.
        review.refresh_from_db()
        _, ok = run(request, intake.update_intake, request.user, review, data,
                    scope_tags=form.cleaned_data["scope_tags"], success="Intake updated.")
        if ok:
            if "continue" in request.POST:
                return redirect("reviews:triage", pk=review.pk)
            return redirect("reviews:detail", pk=review.pk)
    return render(request, "reviews/intake_form.html",
                  review_context(request, review, "overview", form=form, creating=False, step=1))


def triage_view(request, pk):
    review = load_review(request, pk)
    rs = triage.rule_set_for(review)
    editable = can(request.user, "review.triage", review)
    questions = list(rs.triage_questions.all()) if rs else []
    answers = triage.current_answers(review)
    form = TriageForm(request.POST or None, questions=questions, initial_answers=answers, disabled=not editable)
    if request.method == "POST":
        if not editable:
            raise HttpForbidden("review.triage")
        if form.is_valid():
            _, ok = run(request, triage.save_triage, request.user, review, form.cleaned_data,
                        success="Triage answers saved.")
            if ok:
                return redirect("reviews:triage", pk=review.pk)
    level_qs = [form[q.code] for q in questions if q.kind == "LEVEL"]
    yes_no_qs = [form[q.code] for q in questions if q.kind != "LEVEL"]
    return render(request, "reviews/triage.html", review_context(
        request, review, "overview", form=form, level_fields=level_qs, yes_no_fields=yes_no_qs,
        editable=editable, preview=triage.triage_preview(review), rule_set=rs,
        problems=generation.generation_problems(review) if editable else [],
        routing_labels={r["code"]: r["label"] for r in (rs.routing_config if rs else [])}, step=2,
    ))


@require_POST
def generate(request, pk):
    review = load_review(request, pk, "review.generate")
    result, ok = run(request, generation.generate_assessment, request.user, review,
                     success="Tailored assessment generated. Rule and catalogue versions are pinned.")
    return redirect("reviews:assessment", pk=review.pk) if ok else redirect("reviews:triage", pk=review.pk)


def assessment_view(request, pk):
    review = load_review(request, pk)
    if not review.generated_at:
        return redirect("reviews:triage", pk=review.pk)
    editable = can(request.user, "review.answer", review)
    if request.method == "POST":
        if not editable:
            raise HttpForbidden("review.answer")
        rq = get_object_or_404(ReviewQuestion, review=review, pk=request.POST.get("question"))
        form = AnswerForm(request.POST)
        if form.is_valid():
            run(request, assessment.save_answer, request.user, review, rq, form.cleaned_data["answer"],
                form.cleaned_data["comment"], success=f"Answer saved for {rq.question.code}.")
        else:
            messages.error(request, "Choose an answer.")
        return redirect(f"{request.path}#q-{rq.pk}")

    questions = list(review.questions.select_related("question", "answered_by").order_by("question__order"))
    evidence_counts = {}
    for rq in questions:
        rrs = review.requirements.filter(control__questions=rq.question, applicability=Applicability.KEEP)
        rq.linked_requirements = list(rrs)
        rq.evidence = list(EvidenceLink.objects.filter(review_requirement__in=rrs).select_related("evidence"))
        evidence_counts[rq.pk] = len(rq.evidence)
    sections = [(name, list(items)) for name, items in groupby(questions, key=lambda q: q.question.section)]
    answered = sum(1 for q in questions if q.answer)
    return render(request, "reviews/assessment.html", review_context(
        request, review, "assessment", sections=sections, answered=answered, total=len(questions),
        editable=editable, answer_choices=Answer.choices, step=3,
        can_upload=editable,
    ))


def question_why(request, pk, question_pk):
    review = load_review(request, pk)
    rq = get_object_or_404(ReviewQuestion, review=review, pk=question_pk)
    if not can(request.user, "question.explain", review, rq):
        raise HttpForbidden("question.explain")
    return render(request, "reviews/why.html", review_context(request, review, "assessment",
                                                              explanation=question_explanation(rq), rq=rq))


@require_POST
def submit(request, pk):
    review = load_review(request, pk, "review.submit")
    _, ok = run(request, assessment.submit_assessment, request.user, review,
                success="Assessment submitted to Information Security for review.")
    return redirect("reviews:detail", pk=review.pk) if ok else redirect("reviews:assessment", pk=review.pk)


def _reason_view(request, pk, action, service, *, title, warning, button, success, danger=False):
    review = load_review(request, pk, action)
    form = ReasonForm(request.POST or None)
    extra = {}
    if action == "review.return_to_draft":
        extra["blockers"] = assessment.reset_blockers(review)
    if request.method == "POST" and form.is_valid():
        _, ok = run(request, service, request.user, review, form.cleaned_data["reason"], success=success)
        if ok:
            return redirect("reviews:detail", pk=review.pk)
    return render(request, "reviews/reason_form.html", review_context(
        request, review, "overview", form=form, title=title, warning=warning, button=button, danger=danger, **extra
    ))


def return_to_draft(request, pk):
    return _reason_view(
        request, pk, "review.return_to_draft", assessment.return_to_draft,
        title="Return assessment to Draft",
        warning="This discards the generated requirements, questions and answers so triage can be corrected. "
                "The audit history and the pinned rule/catalogue versions are preserved. It is refused once "
                "evidence, conclusions, findings, risk or acceptance records exist.",
        button="Reset to Draft", success="Returned to Draft.", danger=True,
    )


def withdraw(request, pk):
    return _reason_view(
        request, pk, "review.withdraw", assessment.withdraw, title="Withdraw Security Review",
        warning="Withdrawal closes the review permanently. It becomes read-only.", button="Withdraw review",
        success="Review withdrawn.", danger=True,
    )


def return_to_requester(request, pk):
    return _reason_view(
        request, pk, "review.return_to_requester", assessment.return_to_requester,
        title="Return to Requester",
        warning="The Requester can change answers again. Conclusions on changed answers will need re-review.",
        button="Return to Requester", success="Returned to the Requester.",
    )


# --- Reviewer workbench -----------------------------------------------------

WORKBENCH_FILTERS = [
    ("all", "All"), ("asked", "Asked"), ("kept_not_asked", "Kept not asked"), ("skipped", "Skipped"),
    ("flagged", "Flagged"), ("unreviewed", "Unreviewed"), ("VERIFIED", "Verified"),
    ("EVIDENCE_REQUIRED", "Evidence Required"), ("SECURITY_GAP", "Security Gap"), ("NOT_APPLICABLE", "Not Applicable"),
]


def workbench(request, pk):
    review = load_review(request, pk, "review.workbench")
    selected = request.GET.get("filter", "all")
    level = request.GET.get("level", "")
    rows = []
    for rr in review.requirements.select_related("control").prefetch_related("control__framework_refs"):
        current, latest = st.conclusion_state(rr)
        questions = list(review.questions.filter(question__in=rr.control.questions.all()).select_related("question"))
        flags = list(rr.flags.filter(resolved_at__isnull=True))
        row = {
            "rr": rr, "conclusion": current, "stale": current is None and latest is not None,
            "questions": questions, "flags": flags,
            "evidence_count": EvidenceLink.objects.filter(review_requirement=rr).count(),
        }
        keep = rr.is_applicable
        status = current.status if current else None
        match = {
            "all": True, "asked": rr.asked, "kept_not_asked": keep and not rr.asked, "skipped": not keep,
            "flagged": bool(flags), "unreviewed": keep and status is None,
        }.get(selected, status == selected)
        if level and rr.level != level:
            match = False
        if match:
            rows.append(row)
    return render(request, "reviews/workbench.html", review_context(
        request, review, "workbench", rows=rows, filters=WORKBENCH_FILTERS, selected=selected, level=level,
        summary=scope_summary(review),
    ))


def requirement_detail(request, pk, rr_pk):
    from apps.assurance.forms import ConclusionForm
    from apps.assurance.services.conclusions import record_conclusion

    review = load_review(request, pk, "review.workbench")
    rr = get_object_or_404(ReviewRequirement.objects.select_related("control"), review=review, pk=rr_pk)
    form = ConclusionForm(request.POST or None)
    if request.method == "POST":
        if not can(request.user, "conclusion.record", review, rr):
            raise HttpForbidden("conclusion.record")
        if form.is_valid():
            _, ok = run(request, record_conclusion, request.user, review, rr, form.cleaned_data["status"],
                        form.cleaned_data["notes"], success=f"Conclusion recorded for {rr.control.code}.")
            if ok:
                return redirect("reviews:requirement", pk=review.pk, rr_pk=rr.pk)
    current, latest = st.conclusion_state(rr)
    return render(request, "reviews/requirement.html", review_context(
        request, review, "workbench", rr=rr, form=form, conclusion=current,
        stale=current is None and latest is not None,
        history=rr.conclusions.select_related("decided_by").order_by("-decided_at", "-pk"),
        questions=review.questions.filter(question__in=rr.control.questions.all()).select_related("question", "answered_by"),
        evidence=EvidenceLink.objects.filter(review_requirement=rr).select_related("evidence__uploaded_by"),
        flags=rr.flags.select_related("source_question__question").order_by("-created_at"),
        findings=rr.findings.all(),
        can_conclude=can(request.user, "conclusion.record", review, rr),
        can_verify=can(request.user, "conclusion.verify", review, rr),
        statuses=ConclusionStatus.choices,
    ))


# --- Technical scope --------------------------------------------------------

def technical_scope(request, pk):
    review = load_review(request, pk)
    if not can(request.user, "technical.view", review):
        raise HttpForbidden("technical.view")
    confirmed = set(
        review.scope_tags.filter(removed_at_confirmation=False).values_list("scope_tag_id", flat=True)
    )
    scope_form = ScopeConfirmForm(initial={"tags": list(confirmed), "note": review.scope_confirmation_note})
    add_form = AddTechnicalItemForm(review=review) if review.technical_catalogue_id else None
    items = review.technical_items.select_related("control").prefetch_related("control__tags", "findings")
    return render(request, "reviews/technical.html", review_context(
        request, review, "technical", items=items, scope_form=scope_form, add_form=add_form,
        scope_tags=review.scope_tags.select_related("scope_tag"),
    ))


@require_POST
def scope_confirm(request, pk):
    review = load_review(request, pk, "scope.confirm")
    form = ScopeConfirmForm(request.POST)
    if form.is_valid():
        run(request, technical.confirm_scope, request.user, review, form.cleaned_data["tags"],
            form.cleaned_data["note"], success="Technical scope confirmed.")
    return redirect("reviews:technical", pk=review.pk)


@require_POST
def technical_add(request, pk):
    review = load_review(request, pk, "technical.edit")
    control = get_object_or_404(TechnicalControl, pk=request.POST.get("control"), catalogue=review.technical_catalogue)
    run(request, technical.add_technical_item, request.user, review, control, success="Catalogue item added.")
    return redirect("reviews:technical", pk=review.pk)


def technical_item(request, pk, item_pk):
    review = load_review(request, pk)
    item = get_object_or_404(ReviewTechnicalItem.objects.select_related("control"), review=review, pk=item_pk)
    if not can(request.user, "technical.view", review, item):
        raise HttpForbidden("technical.view")
    editable = can(request.user, "technical.edit", review, item)
    form = TechnicalItemForm(request.POST or None, initial={
        "included": item.included, "exclusion_reason": item.exclusion_reason, "result": item.result,
        "notes": item.notes,
    })
    if request.method == "POST":
        if not editable:
            raise HttpForbidden("technical.edit")
        if form.is_valid():
            _, ok = run(request, technical.update_technical_item, request.user, review, item,
                        included=form.cleaned_data["included"], exclusion_reason=form.cleaned_data["exclusion_reason"],
                        result=form.cleaned_data["result"], notes=form.cleaned_data["notes"],
                        success="Technical item updated.")
            if ok:
                return redirect("reviews:technical_item", pk=review.pk, item_pk=item.pk)
    return render(request, "reviews/technical_item.html", review_context(
        request, review, "technical", item=item, form=form, editable=editable,
        evidence=EvidenceLink.objects.filter(technical_item=item).select_related("evidence__uploaded_by"),
        findings=item.findings.prefetch_related("actions"),
    ))


# --- Activity log -----------------------------------------------------------

def audit_log(request, pk):
    review = load_review(request, pk, "review.audit")
    events = AuditEvent.objects.filter(review=review).select_related("actor").order_by("-timestamp", "-pk")
    return render(request, "reviews/audit.html", review_context(request, review, "activity", events=events))
