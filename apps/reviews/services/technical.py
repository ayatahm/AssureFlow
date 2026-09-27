from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.content.models import ScopeTag, TechnicalControl
from apps.core.errors import ServiceError
from apps.reviews.models import Review, ReviewScopeTag, ReviewTechnicalItem, TechnicalResult

from .common import require


@transaction.atomic
def confirm_scope(actor, review, tags, note="", *, request=None):
    review = Review.objects.select_for_update().get(pk=review.pk)
    require(actor, "scope.confirm", review)
    wanted = {t.pk for t in tags}
    if not wanted and not note.strip():
        raise ServiceError("An empty technical scope needs an explicit reason.")
    now = timezone.now()
    for rst in review.scope_tags.all():
        rst.removed_at_confirmation = rst.scope_tag_id not in wanted
        rst.confirmed_by, rst.confirmed_at = actor, now
        rst.save()
    existing = set(review.scope_tags.values_list("scope_tag_id", flat=True))
    for tag in ScopeTag.objects.filter(pk__in=wanted - existing):
        ReviewScopeTag.objects.create(review=review, scope_tag=tag, proposed_by=actor, confirmed_by=actor, confirmed_at=now)

    added = 0
    present = set(review.technical_items.values_list("control_id", flat=True))
    for tc in TechnicalControl.objects.filter(catalogue=review.technical_catalogue, tags__in=wanted).distinct():
        if tc.pk not in present:
            ReviewTechnicalItem.objects.create(review=review, control=tc, included=True, updated_by=actor)
            added += 1
    excluded = 0
    for item in review.technical_items.filter(included=True, added_by_reviewer=False):
        if not set(item.control.tags.values_list("pk", flat=True)) & wanted:
            item.included = False
            item.exclusion_reason = "Scope tag removed at Reviewer confirmation."
            item.updated_by = actor
            item.save()
            excluded += 1

    review.scope_confirmed_by, review.scope_confirmed_at = actor, now
    review.scope_confirmation_note = note
    review.save()
    record("scope.confirmed", actor=actor, review=review, obj=review, request=request,
           detail={"tags": sorted(ScopeTag.objects.filter(pk__in=wanted).values_list("code", flat=True)),
                   "added_items": added, "excluded_items": excluded, "note": bool(note.strip())})
    return review


@transaction.atomic
def update_technical_item(actor, review, item, *, included, exclusion_reason="", result="", notes="", request=None):
    review = Review.objects.select_for_update().get(pk=review.pk)
    require(actor, "technical.edit", review, item)
    if not included and not exclusion_reason.strip():
        raise ServiceError("Excluding a technical item requires a reason.")
    if result not in TechnicalResult.values:
        raise ServiceError("Invalid result.")
    item = ReviewTechnicalItem.objects.select_for_update().get(pk=item.pk)
    item.included = included
    item.exclusion_reason = "" if included else exclusion_reason
    item.result = result
    item.notes = notes
    item.updated_by = actor
    item.save()
    record("technical.updated", actor=actor, review=review, obj=item, request=request,
           detail={"item": item.control.source_id, "included": included, "result": result})
    return item


@transaction.atomic
def add_technical_item(actor, review, control, *, request=None):
    review = Review.objects.select_for_update().get(pk=review.pk)
    require(actor, "technical.edit", review)
    if control.catalogue_id != review.technical_catalogue_id:
        raise ServiceError("The item must come from the review's pinned technical catalogue.")
    item, created = ReviewTechnicalItem.objects.get_or_create(
        review=review, control=control,
        defaults={"included": True, "added_by_reviewer": True, "updated_by": actor},
    )
    if not created:
        if item.included:
            raise ServiceError("That item is already in scope.")
        item.included, item.exclusion_reason, item.updated_by = True, "", actor
        item.save()
    record("technical.added", actor=actor, review=review, obj=item, request=request,
           detail={"item": control.source_id})
    return item
