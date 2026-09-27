from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role
from apps.assurance.models import Action, ActionHistory, ActionState, EvidenceLink, Finding, Priority
from apps.audit.services import record
from apps.core.errors import ServiceError
from apps.reviews.models import ParticipantRole, Review, ReviewRequirement, ReviewTechnicalItem
from apps.reviews.services.common import require


def _eligible_owner(review, owner):
    if owner is None or not owner.is_active or owner.is_app_admin or owner.is_superuser:
        return False
    return (
        owner.has_role(Role.REQUESTER)
        and review.assignments.filter(user=owner, role=ParticipantRole.REQUESTER).exists()
    )


@transaction.atomic
def raise_finding(actor, review, target, *, title, description, priority, request=None):
    review = Review.objects.select_for_update().get(pk=review.pk)
    require(actor, "finding.raise", review, target)
    if priority not in Priority.values:
        raise ServiceError("Invalid priority.")
    if not title.strip() or not description.strip():
        raise ServiceError("Title and description are required.")
    kwargs = {}
    if isinstance(target, ReviewRequirement):
        if not target.is_applicable:
            raise ServiceError("Findings are raised against applicable requirements.")
        kwargs["review_requirement"] = target
    elif isinstance(target, ReviewTechnicalItem):
        kwargs["technical_item"] = target
    else:
        raise ServiceError("A finding needs exactly one requirement or technical item.")
    finding = Finding.objects.create(
        review=review, title=title, description=description, priority=priority, created_by=actor, **kwargs
    )
    record("finding.raised", actor=actor, review=review, obj=finding, request=request,
           detail={"origin": "requirement" if "review_requirement" in kwargs else "technical_item",
                   "origin_code": getattr(target.control, "code", None) or target.control.source_id,
                   "priority": priority})
    return finding


@transaction.atomic
def add_action(actor, finding, *, owner, due_date, expected_outcome, request=None):
    review = Review.objects.select_for_update().get(pk=finding.review_id)
    require(actor, "finding.raise", review)
    if not _eligible_owner(review, owner):
        raise ServiceError("The action owner must be an assigned Requester on this review.")
    if owner.pk == actor.pk:
        raise ServiceError("A Reviewer cannot own an action they will verify.")
    if not expected_outcome.strip():
        raise ServiceError("Expected outcome is required.")
    action = Action.objects.create(
        finding=finding, owner=owner, due_date=due_date, expected_outcome=expected_outcome, created_by=actor
    )
    ActionHistory.objects.create(action=action, kind=ActionHistory.Kind.CREATED, note=expected_outcome, actor=actor)
    record("action.created", actor=actor, review=review, obj=action, request=request,
           detail={"finding": finding.pk, "owner": owner.pk, "due_date": due_date.isoformat()})
    return action


def _lock(action):
    return Action.objects.select_for_update().select_related("finding").get(pk=action.pk)


@transaction.atomic
def submit_action(actor, action, note, evidence_link_ids=(), *, request=None):
    review = Review.objects.select_for_update().get(pk=action.finding.review_id)
    action = _lock(action)
    require(actor, "action.submit", review, action)
    if not note.strip():
        raise ServiceError("Describe the remediation you completed.")
    ids = sorted({int(i) for i in evidence_link_ids})
    valid = set(EvidenceLink.objects.filter(action=action, pk__in=ids).values_list("pk", flat=True))
    if valid != set(ids):
        raise ServiceError("Selected evidence must be linked to this action.")
    action.state = ActionState.SUBMITTED
    action.submission_note = note
    action.submitted_by = actor
    action.submitted_at = timezone.now()
    action.save()
    ActionHistory.objects.create(action=action, kind=ActionHistory.Kind.SUBMITTED, note=note, actor=actor,
                                 evidence_link_ids=ids)
    record("action.submitted", actor=actor, review=review, obj=action, request=request,
           detail={"evidence_link_ids": ids})
    return action


@transaction.atomic
def close_action(actor, action, note="", *, request=None):
    review = Review.objects.select_for_update().get(pk=action.finding.review_id)
    action = _lock(action)
    require(actor, "action.close", review, action)
    action.state = ActionState.CLOSED
    action.closed_by = actor
    action.closed_at = timezone.now()
    action.save()
    ActionHistory.objects.create(action=action, kind=ActionHistory.Kind.CLOSED, note=note, actor=actor)
    record("action.closed", actor=actor, review=review, obj=action, request=request)
    return action


@transaction.atomic
def return_action(actor, action, reason, *, request=None):
    review = Review.objects.select_for_update().get(pk=action.finding.review_id)
    action = _lock(action)
    require(actor, "action.return", review, action)
    if not reason.strip():
        raise ServiceError("A reason is required.")
    action.state = ActionState.OPEN
    action.save()
    ActionHistory.objects.create(action=action, kind=ActionHistory.Kind.RETURNED, note=reason, actor=actor)
    record("action.returned", actor=actor, review=review, obj=action, request=request)
    return action


@transaction.atomic
def reopen_action(actor, action, reason, *, request=None):
    review = Review.objects.select_for_update().get(pk=action.finding.review_id)
    action = _lock(action)
    require(actor, "action.reopen", review, action)
    if not reason.strip():
        raise ServiceError("A reason is required.")
    action.state = ActionState.OPEN
    action.closed_by = None
    action.closed_at = None
    action.save()
    ActionHistory.objects.create(action=action, kind=ActionHistory.Kind.REOPENED, note=reason, actor=actor)
    record("action.reopened", actor=actor, review=review, obj=action, request=request)
    return action
