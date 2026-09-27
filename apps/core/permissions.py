"""Central visibility and action-permission API (spec 22).

* ``visible_reviews(user)`` — assignment-filtered queryset; Admin/superuser see none.
* ``can(user, action, review, obj=None)`` — the single authoritative check used
  by views *and* services. Unknown actions fail closed.
"""
from django.http import Http404

from apps.accounts.models import Role
from apps.reviews.models import ParticipantRole as P
from apps.reviews.models import Review, ReviewAssignment
from apps.reviews.models import ReviewState as S

# Application role required for an assignment to be effective.
ROLE_FOR_PARTICIPANT = {
    P.REQUESTER: Role.REQUESTER,
    P.REVIEWER: Role.REVIEWER,
    P.BUSINESS_OWNER: Role.BUSINESS_OWNER,
}


class PermissionDenied(Exception):
    """Raised by services when ``can`` refuses an action."""


def is_fully_authenticated(user):
    """Active user who completed the configured sign-in flow for this request.

    ``af_auth_complete`` is set by AuthStageMiddleware only after password,
    email-code verification (when required) and any forced password change.
    """
    return bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and user.is_active
        and getattr(user, "af_auth_complete", False)
    )


def is_business_user(user):
    """Admin and technical superuser accounts are never business participants."""
    return is_fully_authenticated(user) and not user.is_superuser and not user.is_app_admin


def effective_participant_roles(user):
    return [p for p, role in ROLE_FOR_PARTICIPANT.items() if user.has_role(role)]


def participant_roles(user, review):
    """Effective participant roles, evaluated fresh on every call so removed
    assignments or changed application roles apply immediately."""
    if not is_business_user(user) or review is None or review.pk is None:
        return frozenset()
    assigned = ReviewAssignment.objects.filter(review_id=review.pk, user_id=user.pk).values_list("role", flat=True)
    allowed = set(effective_participant_roles(user))
    return frozenset(r for r in assigned if r in allowed)


def clear_permission_cache(user):
    if hasattr(user, "clear_role_cache"):
        user.clear_role_cache()


def visible_reviews(user):
    if not is_business_user(user):
        return Review.objects.none()
    roles = effective_participant_roles(user)
    return Review.objects.filter(assignments__user=user, assignments__role__in=roles).distinct()


def get_visible_review_or_404(user, pk):
    try:
        return visible_reviews(user).get(pk=pk)
    except (Review.DoesNotExist, ValueError, TypeError):
        raise Http404("Review not found")


def can_create_review(user):
    return is_business_user(user) and (user.has_role(Role.REQUESTER) or user.has_role(Role.REVIEWER))


def business_owner_targets(user, review):
    """(requirement ids, technical item ids) covered by acceptances assigned to this Business Owner."""
    from apps.assurance.models import BusinessRiskAcceptanceItem

    items = BusinessRiskAcceptanceItem.objects.filter(
        acceptance__review_id=review.pk, acceptance__business_owner_id=user.pk
    ).values_list("review_requirement_id", "technical_item_id")
    reqs = {r for r, _ in items if r}
    techs = {t for _, t in items if t}
    return reqs, techs


# --- rule helpers ---------------------------------------------------------

def _same_review(obj, review):
    if obj is None:
        return False
    review_id = getattr(obj, "review_id", None)
    if review_id is None and hasattr(obj, "finding"):
        review_id = obj.finding.review_id
    return review_id == review.pk


def _author_or_reviewer(roles):
    return bool(roles & {P.REQUESTER, P.REVIEWER})


def _upload(user, review, roles, target):
    from apps.assurance.models import Action, ActionState
    from apps.reviews.models import ReviewRequirement, ReviewTechnicalItem

    if not _same_review(target, review):
        return False
    if isinstance(target, ReviewRequirement):
        if not target.is_applicable:
            return False
        if review.state == S.IN_ASSESSMENT:
            return _author_or_reviewer(roles)
        return review.state == S.IN_REVIEW and P.REVIEWER in roles
    if isinstance(target, ReviewTechnicalItem):
        return review.state in (S.IN_ASSESSMENT, S.IN_REVIEW) and _author_or_reviewer(roles)
    if isinstance(target, Action):
        return (
            review.state == S.IN_REVIEW
            and target.state == ActionState.OPEN
            and target.owner_id == user.pk
            and bool(roles)
        )
    return False


def _evidence_view(user, review, roles, evidence):
    if not _same_review(evidence, review):
        return False
    if _author_or_reviewer(roles):
        return True
    if P.BUSINESS_OWNER in roles:
        reqs, techs = business_owner_targets(user, review)
        for link in evidence.links.all():
            if link.review_requirement_id in reqs or link.technical_item_id in techs:
                return True
            if link.action_id:
                f = link.action.finding
                if f.review_requirement_id in reqs or f.technical_item_id in techs:
                    return True
    return False


def _verify_sod(user, review, roles, rr):
    """A Reviewer may not verify a requirement whose evidence or answers they authored (spec 22.2)."""
    from apps.assurance.models import EvidenceLink

    if P.REVIEWER not in roles or not _same_review(rr, review):
        return False
    if EvidenceLink.objects.filter(review_requirement=rr, evidence__uploaded_by=user).exists():
        return False
    question_ids = rr.control.questions.values_list("pk", flat=True)
    authored = review.questions.filter(question_id__in=question_ids, answered_by=user).exists()
    return not authored


def _trace(user, review, roles, rr):
    if not _same_review(rr, review):
        return False
    if _author_or_reviewer(roles):
        return True
    if P.BUSINESS_OWNER in roles:
        reqs, _ = business_owner_targets(user, review)
        return rr.pk in reqs
    return False


def _acceptance_decide(user, review, roles, acc):
    from apps.assurance.models import AcceptanceState

    return (
        review.state == S.AWAITING_ACCEPTANCE
        and _same_review(acc, review)
        and acc.state == AcceptanceState.PENDING
        and acc.business_owner_id == user.pk
        and P.BUSINESS_OWNER in roles
        and P.REVIEWER not in roles
        and acc.created_by_id != user.pk
    )


def _action_submit(user, review, roles, action):
    from apps.assurance.models import ActionState

    return (
        review.state == S.IN_REVIEW
        and _same_review(action, review)
        and action.state == ActionState.OPEN
        and action.owner_id == user.pk
        and bool(roles)
    )


def _action_review(user, review, roles, action):
    from apps.assurance.models import ActionState

    return (
        review.state == S.IN_REVIEW
        and P.REVIEWER in roles
        and _same_review(action, review)
        and action.state == ActionState.SUBMITTED
        and action.owner_id != user.pk
        and action.submitted_by_id != user.pk
    )


def _action_reopen(user, review, roles, action):
    from apps.assurance.models import ActionState

    return (
        review.state == S.IN_REVIEW
        and P.REVIEWER in roles
        and _same_review(action, review)
        and action.state == ActionState.CLOSED
        and action.owner_id != user.pk
    )


def _finding_view(user, review, roles, finding):
    if not _same_review(finding, review):
        return False
    if _author_or_reviewer(roles):
        return True
    if P.BUSINESS_OWNER in roles:
        reqs, techs = business_owner_targets(user, review)
        return finding.review_requirement_id in reqs or finding.technical_item_id in techs
    return False


RULES = {
    "review.view": lambda u, r, roles, o: bool(roles),
    "review.edit_intake": lambda u, r, roles, o: r.state == S.DRAFT and _author_or_reviewer(roles),
    "review.triage": lambda u, r, roles, o: r.state == S.DRAFT and _author_or_reviewer(roles),
    "review.generate": lambda u, r, roles, o: r.state == S.DRAFT and _author_or_reviewer(roles),
    "review.answer": lambda u, r, roles, o: r.state == S.IN_ASSESSMENT and _author_or_reviewer(roles),
    "review.submit": lambda u, r, roles, o: r.state == S.IN_ASSESSMENT and _author_or_reviewer(roles),
    "review.return_to_draft": lambda u, r, roles, o: r.state == S.IN_ASSESSMENT and P.REVIEWER in roles,
    "review.withdraw": lambda u, r, roles, o: (
        (r.state == S.DRAFT and P.REQUESTER in roles) or (P.REVIEWER in roles and not r.is_frozen)
    ),
    "review.return_to_requester": lambda u, r, roles, o: r.state == S.IN_REVIEW and P.REVIEWER in roles,
    "review.complete": lambda u, r, roles, o: r.state == S.IN_REVIEW and P.REVIEWER in roles,
    "review.audit": lambda u, r, roles, o: P.REVIEWER in roles,
    "review.workbench": lambda u, r, roles, o: P.REVIEWER in roles,
    "question.explain": lambda u, r, roles, o: _author_or_reviewer(roles) and _same_review(o, r),
    "scope.confirm": lambda u, r, roles, o: r.state == S.IN_REVIEW and P.REVIEWER in roles,
    "technical.edit": lambda u, r, roles, o: r.state == S.IN_REVIEW and P.REVIEWER in roles
    and (o is None or _same_review(o, r)),
    "technical.view": lambda u, r, roles, o: _author_or_reviewer(roles) and (o is None or _same_review(o, r)),
    "evidence.upload": _upload,
    "evidence.download": _evidence_view,
    "conclusion.record": lambda u, r, roles, o: r.state == S.IN_REVIEW and P.REVIEWER in roles
    and _same_review(o, r) and o.is_applicable,
    "conclusion.verify": lambda u, r, roles, o: r.state == S.IN_REVIEW and _verify_sod(u, r, roles, o),
    "finding.raise": lambda u, r, roles, o: r.state == S.IN_REVIEW and P.REVIEWER in roles
    and (o is None or _same_review(o, r)),
    "finding.view": _finding_view,
    "findings.list": lambda u, r, roles, o: _author_or_reviewer(roles),
    "action.submit": _action_submit,
    "action.close": _action_review,
    "action.return": _action_review,
    "action.reopen": _action_reopen,
    "compliance.record": lambda u, r, roles, o: r.state == S.IN_REVIEW and _author_or_reviewer(roles)
    and _same_review(o, r) and o.is_applicable,
    "compliance.grant_exception": lambda u, r, roles, o: r.state == S.IN_REVIEW and P.REVIEWER in roles
    and _same_review(o, r) and o.is_applicable,
    "risk.view": lambda u, r, roles, o: bool(roles),
    "risk.record": lambda u, r, roles, o: r.state == S.IN_REVIEW and P.REVIEWER in roles,
    "acceptance.request": lambda u, r, roles, o: r.state == S.IN_REVIEW and P.REVIEWER in roles,
    "acceptance.view": lambda u, r, roles, o: _same_review(o, r) and (
        _author_or_reviewer(roles) or (P.BUSINESS_OWNER in roles and o.business_owner_id == u.pk)
    ),
    "acceptance.decide": _acceptance_decide,
    "trace.full": _trace,
    "report.view": lambda u, r, roles, o: r.state == S.COMPLETED and bool(roles),
    "report.preview": lambda u, r, roles, o: r.state in (S.IN_REVIEW, S.AWAITING_ACCEPTANCE) and P.REVIEWER in roles,
}


def can(user, action, review, obj=None):
    rule = RULES.get(action)
    if rule is None or review is None:
        return False
    roles = participant_roles(user, review)
    if not roles:
        return False
    try:
        return bool(rule(user, review, roles, obj))
    except AttributeError:
        return False


def require(user, action, review, obj=None):
    if not can(user, action, review, obj):
        raise PermissionDenied(action)
