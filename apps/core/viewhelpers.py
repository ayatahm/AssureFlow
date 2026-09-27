"""Small helpers so views stay thin: load, authorise, call a service, report errors."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied as HttpForbidden

from apps.assurance.services import status as st
from apps.core.errors import ServiceError
from apps.core.permissions import PermissionDenied, can, get_visible_review_or_404, participant_roles
from apps.reviews.models import ParticipantRole


def load_review(request, pk, action="review.view", obj=None):
    """404 when the review is not visible to this user; 403 when visible but not permitted."""
    review = get_visible_review_or_404(request.user, pk)
    if action and not can(request.user, action, review, obj):
        raise HttpForbidden(action)
    return review


def run(request, fn, *args, success=None, **kwargs):
    """Call a service; translate refusals into messages. Returns (result, ok)."""
    try:
        result = fn(*args, request=request, **kwargs)
    except ServiceError as exc:
        messages.error(request, exc.message)
        for err in exc.errors:
            messages.warning(request, err)
        return None, False
    except PermissionDenied as exc:
        raise HttpForbidden(str(exc)) from exc
    if success:
        messages.success(request, success)
    return result, True


def primary_role(user, review):
    roles = participant_roles(user, review)
    for role in (ParticipantRole.REVIEWER, ParticipantRole.REQUESTER, ParticipantRole.BUSINESS_OWNER):
        if role in roles:
            return role
    return None


def review_context(request, review, tab, **extra):
    user = request.user
    role = primary_role(user, review)
    ctx = {
        "review": review,
        "tab": tab,
        "viewer_role": role,
        "is_reviewer": role == ParticipantRole.REVIEWER,
        "is_requester": role == ParticipantRole.REQUESTER,
        "is_business_owner": role == ParticipantRole.BUSINESS_OWNER,
        "participants": review.assignments.select_related("user").order_by("role"),
        "latest_risk": review.final_risk_record or st.latest_risk(review),
        "perm": {a.replace(".", "_"): can(user, a, review) for a in (
            "review.edit_intake", "review.triage", "review.generate", "review.answer", "review.submit",
            "review.return_to_draft", "review.withdraw", "review.return_to_requester", "review.complete",
            "review.audit", "review.workbench", "scope.confirm", "technical.edit", "finding.raise",
            "findings.list", "risk.record", "acceptance.request", "report.view", "report.preview",
        )},
    }
    ctx.update(extra)
    return ctx
