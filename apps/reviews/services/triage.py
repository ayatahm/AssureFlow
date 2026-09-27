from django.db import transaction

from apps.audit.services import record
from apps.content.loader import build_engine_config
from apps.content.models import RuleSet
from apps.core.errors import ServiceError
from apps.engine import engine
from apps.reviews.models import Review, TriageAnswer

from .common import acting_on_behalf, require


def rule_set_for(review):
    """The pinned rule set, or the active one for a not-yet-generated review."""
    return review.rule_set or RuleSet.objects.filter(is_active=True).first()


def current_answers(review):
    return dict(review.triage_answers.values_list("question_code", "value"))


@transaction.atomic
def save_triage(actor, review, answers, *, request=None):
    review = Review.objects.select_for_update().get(pk=review.pk)
    require(actor, "review.triage", review)
    rs = rule_set_for(review)
    if rs is None:
        raise ServiceError("No active rule set has been loaded.")
    questions = {q.code: q for q in rs.triage_questions.all()}
    on_behalf = acting_on_behalf(actor, review)
    saved = []
    for code, value in answers.items():
        if code not in questions:
            raise ServiceError(f"Unknown triage question {code}.")
        if value in (None, ""):
            continue  # never default an answer
        q = questions[code]
        allowed = engine.YES_NO if q.kind == "YES_NO" else tuple(q.options)
        if value not in allowed:
            raise ServiceError(f"Invalid answer for {code}.")
        obj, created = TriageAnswer.objects.get_or_create(
            review=review, question_code=code,
            defaults={"value": value, "entered_by": actor, "on_behalf": on_behalf},
        )
        if not created and obj.value != value:
            obj.value, obj.entered_by, obj.on_behalf = value, actor, on_behalf
            obj.save()
        if created or obj.entered_by_id == actor.pk:
            saved.append(code)
    if "impact" in answers and answers["impact"]:
        review.impact = answers["impact"]
        review.save(update_fields=["impact", "updated_at"])
    record("triage.answered", actor=actor, review=review, obj=review, request=request,
           detail={"questions": sorted(saved), "on_behalf": on_behalf})
    return review


def triage_preview(review):
    rs = rule_set_for(review)
    if rs is None:
        return None
    return engine.preview(build_engine_config(rs), current_answers(review))
