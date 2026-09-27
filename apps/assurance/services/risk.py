from django.db import transaction

from apps.assurance.models import RISK_SCALE_VERSION, RiskRating, RiskRecord
from apps.audit.services import record
from apps.core.errors import ServiceError
from apps.reviews.models import Review
from apps.reviews.services.common import require


@transaction.atomic
def record_risk(actor, review, *, inherent, residual, rationale, request=None):
    """Append a new authoritative RiskRecord. Ratings are Reviewer judgement (spec 13)."""
    review = Review.objects.select_for_update().get(pk=review.pk)
    require(actor, "risk.record", review)
    if inherent not in RiskRating.values or residual not in RiskRating.values:
        raise ServiceError("Choose ratings from the configured scale.")
    if not rationale.strip():
        raise ServiceError("A rationale is required.")
    rec = RiskRecord.objects.create(
        review=review, inherent=inherent, residual=residual, rationale=rationale,
        scale_version=RISK_SCALE_VERSION, scale_labels=[label for _, label in RiskRating.choices], assessed_by=actor,
    )
    record("risk.recorded", actor=actor, review=review, obj=rec, request=request,
           detail={"inherent": inherent, "residual": residual})
    return rec
