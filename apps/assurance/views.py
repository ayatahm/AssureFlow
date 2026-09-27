from django.core.exceptions import PermissionDenied as HttpForbidden
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.assurance.models import (
    Action,
    ActionState,
    BusinessRiskAcceptance,
    Evidence,
    EvidenceLink,
    Finding,
)
from apps.assurance.services import acceptance as acceptance_svc
from apps.assurance.services import compliance as compliance_svc
from apps.assurance.services import completion as completion_svc
from apps.assurance.services import evidence as evidence_svc
from apps.assurance.services import findings as findings_svc
from apps.assurance.services import risk as risk_svc
from apps.assurance.services import status as st
from apps.core.errors import ServiceError
from apps.core.permissions import business_owner_targets, can, get_visible_review_or_404
from apps.core.viewhelpers import load_review, primary_role, review_context, run
from apps.reporting.queries import build_trace
from apps.reviews.models import ParticipantRole, ReviewRequirement

from .forms import (
    AcceptanceDecisionForm,
    AcceptanceRequestForm,
    ActionForm,
    ActionSubmitForm,
    ComplianceForm,
    CompletionForm,
    EvidenceUploadForm,
    FindingForm,
    NoteForm,
    RiskForm,
)

TARGET_KINDS = ("requirement", "technical", "action")


def _safe_next(request, url):
    """Only same-host relative paths are accepted as post-upload destinations."""
    from django.utils.http import url_has_allowed_host_and_scheme

    if url and url.startswith("/") and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}):
        return url
    return ""


def _parse_target(review, raw):
    kind, _, pk = (raw or "").partition(":")
    if kind not in TARGET_KINDS:
        raise Http404("Unknown evidence target")
    try:
        return evidence_svc.resolve_target(review, kind, pk)
    except ServiceError:
        raise Http404("Evidence target not found")


# --- Evidence ---------------------------------------------------------------

def evidence_list(request, pk):
    review = load_review(request, pk)
    role = primary_role(request.user, review)
    links = EvidenceLink.objects.filter(evidence__review=review).select_related(
        "evidence__uploaded_by", "review_requirement__control", "technical_item__control", "action__finding"
    ).order_by("-evidence__uploaded_at")
    if role == ParticipantRole.BUSINESS_OWNER:
        links = [link for link in links if can(request.user, "evidence.download", review, link.evidence)]
    return render(request, "assurance/evidence_list.html", review_context(request, review, "evidence", links=links))


def evidence_upload(request, pk):
    review = load_review(request, pk)
    raw = request.GET.get("target") or request.POST.get("target")
    target = _parse_target(review, raw)
    if not can(request.user, "evidence.upload", review, target):
        raise HttpForbidden("evidence.upload")
    form = EvidenceUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        _, ok = run(request, evidence_svc.upload_evidence, request.user, review, target, form.cleaned_data["file"],
                    form.cleaned_data["comment"], success="Evidence uploaded. Uploading never verifies a control.")
        if ok:
            return redirect(_safe_next(request, request.POST.get("next")) or request.get_full_path())
    existing = EvidenceLink.objects.filter(**evidence_svc.link_kwargs(target)).select_related("evidence__uploaded_by")
    from apps.assurance.models import EvidencePolicy

    policy = EvidencePolicy.current()
    return render(request, "assurance/evidence_upload.html", review_context(
        request, review, "evidence", form=form, target=target, target_raw=raw,
        target_label=evidence_svc.target_label(target), existing=existing, policy=policy,
        next=_safe_next(request, request.GET.get("next", "")),
    ))


def evidence_download(request, evidence_pk):
    evidence = get_object_or_404(Evidence, pk=evidence_pk)
    # Visibility first: an unassigned user gets 404, not a hint that the file exists.
    review = get_visible_review_or_404(request.user, evidence.review_id)
    if not can(request.user, "evidence.download", review, evidence):
        raise Http404("Evidence not found")
    try:
        handle = evidence_svc.open_for_download(request.user, evidence, request=request)
    except ServiceError as exc:
        raise Http404(exc.message)
    return FileResponse(handle, as_attachment=True, filename=evidence.original_name, content_type=evidence.content_type)


# --- Findings and actions ---------------------------------------------------

def findings_list(request, pk):
    review = load_review(request, pk)
    if not can(request.user, "findings.list", review):
        raise HttpForbidden("findings.list")
    findings = review.findings.select_related("review_requirement__control", "technical_item__control").prefetch_related(
        "actions__owner"
    )
    actions = Action.objects.filter(finding__review=review).select_related("finding", "owner")
    tab = request.GET.get("tab", "findings")
    return render(request, "assurance/findings.html", review_context(
        request, review, "findings", findings=findings, actions=actions, list_tab=tab,
        open_actions=actions.exclude(state=ActionState.CLOSED), closed_actions=actions.filter(state=ActionState.CLOSED),
    ))


def finding_new(request, pk):
    review = load_review(request, pk, "finding.raise")
    raw = request.GET.get("target") or request.POST.get("target")
    kind, _, target_pk = (raw or "").partition(":")
    if kind not in ("requirement", "technical"):
        raise Http404("Choose a requirement or technical item")
    try:
        target = evidence_svc.resolve_target(review, kind, target_pk)
    except ServiceError:
        raise Http404("Target not found")
    form = FindingForm(request.POST or None, review=review)
    if request.method == "POST" and form.is_valid():
        from django.db import transaction

        try:
            with transaction.atomic():
                finding = findings_svc.raise_finding(
                    request.user, review, target, title=form.cleaned_data["title"],
                    description=form.cleaned_data["description"], priority=form.cleaned_data["priority"],
                    request=request,
                )
                findings_svc.add_action(
                    request.user, finding, owner=form.cleaned_data["owner"], due_date=form.cleaned_data["due_date"],
                    expected_outcome=form.cleaned_data["expected_outcome"], request=request,
                )
        except ServiceError as exc:
            form.add_error(None, exc.message)
        else:
            from django.contrib import messages

            messages.success(request, f"Finding {finding.code} raised with a remediation action.")
            return redirect("assurance:finding", pk=review.pk, finding_pk=finding.pk)
    return render(request, "assurance/finding_new.html", review_context(
        request, review, "findings", form=form, target=target, target_raw=raw,
        target_label=evidence_svc.target_label(target),
    ))


def finding_detail(request, pk, finding_pk):
    review = load_review(request, pk)
    finding = get_object_or_404(Finding, review=review, pk=finding_pk)
    if not can(request.user, "finding.view", review, finding):
        raise Http404("Finding not found")
    form = ActionForm(request.POST or None, review=review)
    if request.method == "POST":
        if not can(request.user, "finding.raise", review, finding.target):
            raise HttpForbidden("finding.raise")
        if form.is_valid():
            _, ok = run(request, findings_svc.add_action, request.user, finding, owner=form.cleaned_data["owner"],
                        due_date=form.cleaned_data["due_date"],
                        expected_outcome=form.cleaned_data["expected_outcome"], success="Action added.")
            if ok:
                return redirect("assurance:finding", pk=review.pk, finding_pk=finding.pk)
    status, acc = st.target_treatment(review, finding.target, review.final_risk_record or st.latest_risk(review))
    return render(request, "assurance/finding_detail.html", review_context(
        request, review, "findings", finding=finding, form=form,
        actions=finding.actions.select_related("owner", "closed_by").prefetch_related("history__actor",
                                                                                         "evidence_links__evidence"),
        treatment=status, treatment_label=st.TREATMENT_LABELS[status], treatment_acceptance=acc,
        can_add_action=can(request.user, "finding.raise", review, finding.target),
    ))


def _action_or_404(request, action_pk):
    action = get_object_or_404(Action.objects.select_related("finding"), pk=action_pk)
    review = get_visible_review_or_404(request.user, action.finding.review_id)
    if not can(request.user, "finding.view", review, action.finding):
        raise Http404("Action not found")
    return action, review


def action_detail(request, action_pk):
    action, review = _action_or_404(request, action_pk)
    submit_form = ActionSubmitForm(action=action)
    return render(request, "assurance/action_detail.html", review_context(
        request, review, "findings", action=action, finding=action.finding, submit_form=submit_form,
        note_form=NoteForm(), history=action.history.select_related("actor"),
        evidence=action.evidence_links.select_related("evidence__uploaded_by"),
        can_submit=can(request.user, "action.submit", review, action),
        can_upload=can(request.user, "evidence.upload", review, action),
        can_close=can(request.user, "action.close", review, action),
        can_reopen=can(request.user, "action.reopen", review, action),
    ))


@require_POST
def action_submit(request, action_pk):
    action, review = _action_or_404(request, action_pk)
    form = ActionSubmitForm(request.POST, action=action)
    if form.is_valid():
        run(request, findings_svc.submit_action, request.user, action, form.cleaned_data["note"],
            form.cleaned_data["evidence_links"], success="Remediation submitted for Reviewer verification.")
    else:
        from django.contrib import messages

        messages.error(request, "Describe the remediation you completed.")
    return redirect("assurance:action", action_pk=action.pk)


@require_POST
def action_close(request, action_pk):
    action, review = _action_or_404(request, action_pk)
    form = NoteForm(request.POST)
    form.is_valid()
    if request.POST.get("decision") == "return":
        run(request, findings_svc.return_action, request.user, action, form.cleaned_data.get("note", ""),
            success="Submission returned to the action owner.")
    else:
        run(request, findings_svc.close_action, request.user, action, form.cleaned_data.get("note", ""),
            success="Action closed. Record the control conclusion separately — closing does not verify it.")
    return redirect("assurance:action", action_pk=action.pk)


@require_POST
def action_reopen(request, action_pk):
    action, review = _action_or_404(request, action_pk)
    form = NoteForm(request.POST)
    form.is_valid()
    run(request, findings_svc.reopen_action, request.user, action, form.cleaned_data.get("note", ""),
        success="Action reopened.")
    return redirect("assurance:action", action_pk=action.pk)


# --- Compliance and risk ----------------------------------------------------

def compliance_risk(request, pk):
    review = load_review(request, pk, "risk.view")
    gaps = st.requirement_gaps(review)
    rows = []
    for rr in gaps:
        rows.append({"rr": rr, "conclusion": st.current_conclusion(rr), "decision": st.current_compliance(rr),
                     "guidance": compliance_svc.guidance_table(rr)})
    risk_form = RiskForm(request.POST or None)
    if request.method == "POST":
        if not can(request.user, "risk.record", review):
            raise HttpForbidden("risk.record")
        if risk_form.is_valid():
            _, ok = run(request, risk_svc.record_risk, request.user, review, success="Risk judgement recorded.",
                        **risk_form.cleaned_data)
            if ok:
                return redirect("assurance:risk", pk=review.pk)
    history = review.risk_records.select_related("assessed_by").order_by("-assessed_at", "-pk")
    return render(request, "assurance/compliance_risk.html", review_context(
        request, review, "risk", rows=rows, risk_form=risk_form, risk_history=history,
        technical_unresolved=st.unresolved_technical_items(review),
    ))


def compliance_decision(request, pk, rr_pk):
    review = load_review(request, pk)
    rr = get_object_or_404(ReviewRequirement.objects.select_related("control"), review=review, pk=rr_pk)
    if not can(request.user, "compliance.record", review, rr):
        raise HttpForbidden("compliance.record")
    allow_exception = can(request.user, "compliance.grant_exception", review, rr)
    form = ComplianceForm(request.POST or None, allow_exception=allow_exception)
    if request.method == "POST" and form.is_valid():
        _, ok = run(request, compliance_svc.record_compliance, request.user, review, rr,
                    form.cleaned_data["decision"], form.cleaned_data["note"],
                    success=f"Project Compliance Decision recorded for {rr.control.code}.")
        if ok:
            return redirect("assurance:risk", pk=review.pk)
    return render(request, "assurance/compliance_form.html", review_context(
        request, review, "risk", rr=rr, form=form, guidance=compliance_svc.guidance_table(rr),
        history=rr.compliance_decisions.select_related("decided_by").order_by("-decided_at"),
        conclusion=st.current_conclusion(rr),
    ))


# --- Formal Business Risk Acceptance ---------------------------------------

def acceptance_view(request, pk):
    review = load_review(request, pk)
    role = primary_role(request.user, review)
    acceptances = review.acceptances.select_related("business_owner", "risk_record").prefetch_related(
        "items__review_requirement__control", "items__technical_item__control"
    )
    if role == ParticipantRole.BUSINESS_OWNER:
        acceptances = acceptances.filter(business_owner=request.user)
    form = None
    gaps = technical = []
    if can(request.user, "acceptance.request", review):
        gaps = st.requirement_gaps(review)
        technical = st.unresolved_technical_items(review)
        form = AcceptanceRequestForm(request.POST or None, review=review, gaps=gaps, technical=technical)
        if request.method == "POST" and form.is_valid():
            data = form.cleaned_data
            _, ok = run(
                request, acceptance_svc.request_acceptance, request.user, review,
                business_owner=data["business_owner"], requirements=data["requirements"],
                technical_items=data["technical_items"], justification=data["justification"],
                compensating_controls=data["compensating_controls"], conditions=data["conditions"],
                review_date=data["review_date"], expiry_date=data["expiry_date"],
                success="Formal Business Risk Acceptance requested. The review is Awaiting Acceptance.",
            )
            if ok:
                return redirect("assurance:acceptance", pk=review.pk)
    elif request.method == "POST":
        raise HttpForbidden("acceptance.request")
    return render(request, "assurance/acceptance.html", review_context(
        request, review, "acceptance", acceptances=acceptances, form=form, gaps=gaps, technical=technical,
    ))


def acceptance_decide(request, acceptance_pk):
    acc = get_object_or_404(BusinessRiskAcceptance.objects.select_related("risk_record", "review"), pk=acceptance_pk)
    review = get_visible_review_or_404(request.user, acc.review_id)
    if not can(request.user, "acceptance.view", review, acc):
        raise Http404("Acceptance not found")
    can_decide = can(request.user, "acceptance.decide", review, acc)
    form = AcceptanceDecisionForm(request.POST or None)
    if request.method == "POST":
        if not can_decide:
            raise HttpForbidden("acceptance.decide")
        if form.is_valid():
            _, ok = run(request, acceptance_svc.decide_acceptance, request.user, acc, form.cleaned_data["decision"],
                        form.cleaned_data["note"], success="Decision recorded.")
            if ok:
                return redirect("assurance:decide", acceptance_pk=acc.pk)
    items = []
    for item in acc.items.select_related("review_requirement__control", "technical_item__control"):
        target = item.target
        entry = {"item": item, "target": target}
        if item.review_requirement_id:
            entry["conclusion"] = st.current_conclusion(target)
            entry["compliance"] = st.current_compliance(target)
            entry["evidence"] = EvidenceLink.objects.filter(review_requirement=target).select_related("evidence")
        else:
            entry["findings"] = target.findings.all()
            entry["evidence"] = EvidenceLink.objects.filter(technical_item=target).select_related("evidence")
        items.append(entry)
    return render(request, "assurance/acceptance_decide.html", review_context(
        request, review, "acceptance", acc=acc, items=items, form=form, can_decide=can_decide,
    ))


# --- Completion ---------------------------------------------------------------

def complete(request, pk):
    review = load_review(request, pk, "review.complete")
    chk = completion_svc.evaluate_completion(review)
    form = CompletionForm(request.POST or None, initial={"final_summary": review.final_summary,
                                                         "conditions": review.conditions or "None"})
    if request.method == "POST" and form.is_valid():
        _, ok = run(request, completion_svc.complete_review, request.user, review,
                    final_summary=form.cleaned_data["final_summary"], conditions=form.cleaned_data["conditions"],
                    success="Review completed. Report revision 1 issued.")
        if ok:
            return redirect("reporting:report", pk=review.pk)
        chk = completion_svc.evaluate_completion(review)
    return render(request, "assurance/complete.html", review_context(request, review, "report", chk=chk, form=form))


# --- Assurance Trace ------------------------------------------------------------

def trace(request, pk, rr_pk):
    review = load_review(request, pk)
    rr = get_object_or_404(ReviewRequirement.objects.select_related("control"), review=review, pk=rr_pk)
    if not can(request.user, "trace.full", review, rr):
        raise Http404("Trace not found")
    role = primary_role(request.user, review)
    data = build_trace(review, rr, viewer_role=role)
    if role == ParticipantRole.BUSINESS_OWNER:
        reqs, _ = business_owner_targets(request.user, review)
        data["acceptances"] = [a for a in data["acceptances"] if a.business_owner_id == request.user.pk]
    return render(request, "assurance/trace.html", review_context(request, review, "workbench", t=data))
