from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.models import Role
from apps.assurance.models import AcceptanceState, Action, ActionState, BusinessRiskAcceptance
from apps.core.permissions import can_create_review, is_business_user, visible_reviews
from apps.reviews.models import ParticipantRole, ReviewState


def greeting():
    hour = timezone.localtime().hour
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"


def dashboard(request):
    user = request.user
    ctx = {"greeting": greeting(), "can_create": can_create_review(user)}
    if not is_business_user(user):
        return render(request, "core/dashboard_admin.html", ctx)

    reviews = visible_reviews(user).select_related("department")
    as_requester = reviews.filter(assignments__user=user, assignments__role=ParticipantRole.REQUESTER)
    as_reviewer = reviews.filter(assignments__user=user, assignments__role=ParticipantRole.REVIEWER)
    my_actions = Action.objects.filter(owner=user, finding__review__in=reviews).select_related("finding__review")
    today = timezone.localdate()

    awaiting = set()
    if user.has_role(Role.REQUESTER):
        awaiting |= set(as_requester.filter(state__in=[ReviewState.DRAFT, ReviewState.IN_ASSESSMENT]).values_list("pk", flat=True))
        awaiting |= set(my_actions.filter(state=ActionState.OPEN).values_list("finding__review_id", flat=True))
    if user.has_role(Role.REVIEWER):
        awaiting |= set(as_reviewer.filter(state=ReviewState.IN_REVIEW).values_list("pk", flat=True))
    pending_acceptances = BusinessRiskAcceptance.objects.none()
    if user.has_role(Role.BUSINESS_OWNER):
        pending_acceptances = BusinessRiskAcceptance.objects.filter(
            business_owner=user, state=AcceptanceState.PENDING, review__in=reviews
        ).select_related("review", "risk_record")
        awaiting |= set(pending_acceptances.values_list("review_id", flat=True))

    ctx.update({
        "reviews": reviews.order_by("-updated_at")[:25],
        "metrics": {
            "mine": reviews.count(),
            "awaiting": len(awaiting),
            "in_progress": reviews.exclude(state__in=[ReviewState.COMPLETED, ReviewState.WITHDRAWN]).count(),
            "completed": reviews.filter(state=ReviewState.COMPLETED).count(),
        },
        "my_actions": my_actions.filter(state=ActionState.OPEN).order_by("due_date")[:10],
        "today": today,
        "pending_acceptances": pending_acceptances,
    })
    if user.has_role(Role.REVIEWER):
        reviewer_actions = Action.objects.filter(finding__review__in=as_reviewer).select_related("finding__review", "owner")
        ctx.update({
            "waiting_conclusion": as_reviewer.filter(state=ReviewState.IN_REVIEW),
            "submitted_actions": reviewer_actions.filter(state=ActionState.SUBMITTED),
            "overdue_actions": reviewer_actions.filter(state=ActionState.OPEN, due_date__lt=today),
            "open_findings": reviewer_actions.filter(~Q(state=ActionState.CLOSED)).values("finding").distinct().count(),
            "awaiting_owner": BusinessRiskAcceptance.objects.filter(
                review__in=as_reviewer, state=AcceptanceState.PENDING
            ).select_related("review", "business_owner"),
        })
    if user.has_role(Role.BUSINESS_OWNER):
        ctx["decided_acceptances"] = BusinessRiskAcceptance.objects.filter(
            business_owner=user, review__in=reviews
        ).exclude(state=AcceptanceState.PENDING).select_related("review")[:20]
    return render(request, "core/dashboard.html", ctx)


def error_403(request, exception=None):
    return render(request, "errors/403.html", status=403)


def error_404(request, exception=None):
    return render(request, "errors/404.html", status=404)


def error_500(request):
    return render(request, "errors/500.html", status=500)
