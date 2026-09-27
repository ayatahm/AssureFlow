from django.db import transaction

from apps.audit.services import record
from apps.core.permissions import PermissionDenied, can
from apps.reviews.models import ParticipantRole, Review


def lock_review(review):
    """Re-read and row-lock the review inside the caller's transaction."""
    return Review.objects.select_for_update().get(pk=review.pk)


def require(actor, action, review, obj=None):
    if not can(actor, action, review, obj):
        raise PermissionDenied(action)


def acting_on_behalf(actor, review):
    """True when a Reviewer (not the assigned Requester) enters requester data."""
    roles = set(review.assignments.filter(user=actor).values_list("role", flat=True))
    return ParticipantRole.REQUESTER not in roles


def transition(actor, review, new_state, *, reason="", request=None, detail=None):
    old = review.state
    review.state = new_state
    review.save(update_fields=["state", "updated_at"])
    payload = {"from": old, "to": new_state}
    if reason:
        payload["reason"] = reason
    payload.update(detail or {})
    record("review.transition", actor=actor, review=review, obj=review, detail=payload, request=request)


atomic = transaction.atomic
