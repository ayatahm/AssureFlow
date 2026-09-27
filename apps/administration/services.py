"""Metadata-only participant administration (spec 3.4, 24.4)."""
from django.db import IntegrityError, transaction

from apps.accounts.models import Role
from apps.audit.services import record
from apps.core.errors import ServiceError
from apps.core.permissions import ROLE_FOR_PARTICIPANT, PermissionDenied
from apps.reviews.models import ParticipantRole, Review, ReviewAssignment

SAFE_FIELDS = ("id", "reference", "title", "department__name", "state")


def is_admin_operator(user):
    return bool(user.is_active and getattr(user, "af_auth_complete", False) and (user.is_superuser or user.is_app_admin))


def review_metadata():
    """Explicit safe projection — never full Review objects (spec 22.1)."""
    rows = list(Review.objects.order_by("-created_at").values(*SAFE_FIELDS))
    participants = {}
    for a in ReviewAssignment.objects.select_related("user").order_by("role", "user__full_name"):
        participants.setdefault(a.review_id, []).append(
            {"id": a.pk, "role": a.get_role_display(), "name": a.user.full_name, "employee_id": a.user.employee_id}
        )
    for row in rows:
        row["department"] = row.pop("department__name")
        row["participants"] = participants.get(row["id"], [])
    return rows


def _validate(actor, user, role):
    if not is_admin_operator(actor):
        raise PermissionDenied("admin.assign")
    if role not in ParticipantRole.values:
        raise ServiceError("Unknown participant role.")
    if user.pk == actor.pk:
        raise ServiceError("Administrators cannot assign themselves to a review.")
    if user.is_superuser or user.is_app_admin:
        raise ServiceError("Admin accounts cannot be review participants.")
    if not user.is_active:
        raise ServiceError("The account is disabled.")
    if not user.has_role(ROLE_FOR_PARTICIPANT[role]):
        raise ServiceError(f"{user.full_name} does not hold the {ROLE_FOR_PARTICIPANT[role]} application role.")


@transaction.atomic
def assign(actor, review_id, user, role, *, request=None):
    review = Review.objects.select_for_update().get(pk=review_id)
    _validate(actor, user, role)
    if review.is_frozen:
        raise ServiceError("Completed or withdrawn reviews cannot change participants.")
    existing = set(review.assignments.filter(user=user).values_list("role", flat=True))
    if role == ParticipantRole.REVIEWER and existing & {ParticipantRole.REQUESTER, ParticipantRole.BUSINESS_OWNER}:
        raise ServiceError("A Reviewer cannot also be the Requester or Business Owner on the same review.")
    if role != ParticipantRole.REVIEWER and ParticipantRole.REVIEWER in existing:
        raise ServiceError("This user is already the Reviewer on this review.")
    try:
        with transaction.atomic():
            a = ReviewAssignment.objects.create(review=review, user=user, role=role, assigned_by=actor)
    except IntegrityError as exc:
        raise ServiceError("That assignment already exists.") from exc
    record("admin.assignment_changed", actor=actor, review=review, obj=a, request=request,
           detail={"change": "added", "user": user.employee_id, "role": role})
    return a


@transaction.atomic
def unassign(actor, assignment_id, *, request=None):
    if not is_admin_operator(actor):
        raise PermissionDenied("admin.assign")
    a = ReviewAssignment.objects.select_related("review", "user").get(pk=assignment_id)
    review = Review.objects.select_for_update().get(pk=a.review_id)
    if review.is_frozen:
        raise ServiceError("Completed or withdrawn reviews cannot change participants.")
    if a.role in (ParticipantRole.REQUESTER, ParticipantRole.REVIEWER) and \
            review.assignments.filter(role=a.role).count() == 1:
        raise ServiceError("Assign a replacement before removing the only participant in this role.")
    detail = {"change": "removed", "user": a.user.employee_id, "role": a.role}
    a.delete()
    record("admin.assignment_changed", actor=actor, review=review, obj=None, request=request, detail=detail)


ACCOUNT_EVENT_PREFIXES = ("user.", "admin.")


def account_events(limit=300):
    """Account/assignment summaries only — never assessment audit payloads."""
    from apps.audit.models import AuditEvent
    from django.db.models import Q

    q = Q()
    for prefix in ACCOUNT_EVENT_PREFIXES:
        q |= Q(action__startswith=prefix)
    rows = []
    for e in AuditEvent.objects.filter(q).select_related("actor", "review").order_by("-timestamp", "-pk")[:limit]:
        rows.append({
            "timestamp": e.timestamp, "action": e.action,
            "actor": e.actor.full_name if e.actor else "system",
            "review": e.review.reference if e.review_id else "",
            "summary": {k: v for k, v in e.detail.items() if k in {"target_user", "changed", "roles", "is_active",
                                                                     "change", "user", "role", "reason", "stage",
                                                                     "fields"}},
            "ip": e.ip_address or "",
        })
    return rows
