from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role
from apps.audit.services import record
from apps.content.models import ScopeTag
from apps.core.errors import ServiceError
from apps.core.permissions import PermissionDenied, can_create_review
from apps.reviews.models import (
    ParticipantRole,
    ReferenceCounter,
    Review,
    ReviewAssignment,
    ReviewScopeTag,
)

from .common import require

INTAKE_FIELDS = (
    "title", "purpose", "goals", "department", "project_owner", "request_type", "target_date", "external_ref",
)


def next_reference(year=None):
    year = year or timezone.now().year
    counter, _ = ReferenceCounter.objects.select_for_update().get_or_create(year=year)
    counter.last += 1
    counter.save(update_fields=["last"])
    return f"AF-{year}-{counter.last:04d}"


def _check_participant(user, role):
    if user is None or not user.is_active or user.is_superuser or user.is_app_admin:
        raise ServiceError(f"The selected {role} is not an eligible active account.")
    if not user.has_role(role):
        raise ServiceError(f"{user.full_name} does not hold the {role} role.")


@transaction.atomic
def create_review(actor, data, *, reviewer, requester=None, scope_tags=(), request=None):
    """Create a Draft review. A Reviewer creating it acts on behalf of the named Requester."""
    if not can_create_review(actor):
        raise PermissionDenied("review.create")
    on_behalf = actor.has_role(Role.REVIEWER) and requester is not None and requester.pk != actor.pk
    if not on_behalf:
        if not actor.has_role(Role.REQUESTER):
            raise ServiceError("Choose the Requester this review is raised for.")
        requester = actor
    _check_participant(requester, Role.REQUESTER)
    _check_participant(reviewer, Role.REVIEWER)
    if reviewer.pk == requester.pk:
        raise ServiceError("The Requester and the assigned Reviewer must be different people.")

    review = Review(created_by=actor, on_behalf=on_behalf, reference=next_reference())
    for field in INTAKE_FIELDS:
        if field in data:
            setattr(review, field, data[field])
    review.save()
    for user, role in ((requester, ParticipantRole.REQUESTER), (reviewer, ParticipantRole.REVIEWER)):
        ReviewAssignment.objects.create(review=review, user=user, role=role, assigned_by=actor)
    _set_proposed_tags(actor, review, scope_tags)
    record(
        "review.created", actor=actor, review=review, obj=review, request=request,
        detail={"reference": review.reference, "request_type": review.request_type, "on_behalf": on_behalf,
                "requester": requester.pk, "reviewer": reviewer.pk},
    )
    return review


def _set_proposed_tags(actor, review, tags):
    wanted = {t.pk for t in tags}
    ReviewScopeTag.objects.filter(review=review).exclude(scope_tag_id__in=wanted).delete()
    existing = set(ReviewScopeTag.objects.filter(review=review).values_list("scope_tag_id", flat=True))
    for tag in ScopeTag.objects.filter(pk__in=wanted - existing):
        ReviewScopeTag.objects.create(review=review, scope_tag=tag, proposed_by=actor)


@transaction.atomic
def update_intake(actor, review, data, *, scope_tags=None, request=None):
    review = Review.objects.select_for_update().get(pk=review.pk)
    require(actor, "review.edit_intake", review)
    changed = []
    for field in INTAKE_FIELDS:
        if field in data and getattr(review, field) != data[field]:
            setattr(review, field, data[field])
            changed.append(field)
    review.save()
    if scope_tags is not None:
        before = set(review.scope_tags.values_list("scope_tag__code", flat=True))
        _set_proposed_tags(actor, review, scope_tags)
        after = set(review.scope_tags.values_list("scope_tag__code", flat=True))
        if before != after:
            changed.append("technical_scope")
    if changed:
        record("review.intake_updated", actor=actor, review=review, obj=review, request=request,
               detail={"fields": sorted(changed)})
    return review
