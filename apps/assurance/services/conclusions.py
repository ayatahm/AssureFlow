from django.db import transaction
from django.utils import timezone

from apps.assurance.models import Conclusion, ConclusionStatus
from apps.audit.services import record
from apps.core.errors import ServiceError
from apps.core.permissions import PermissionDenied, can
from apps.reviews.models import Review, ReviewFlag
from apps.reviews.services.common import require


@transaction.atomic
def record_conclusion(actor, review, rr, status, notes="", *, request=None):
    """Only a Reviewer records conclusions; uploads never verify automatically (spec 10)."""
    review = Review.objects.select_for_update().get(pk=review.pk)
    require(actor, "conclusion.record", review, rr)
    if status not in ConclusionStatus.values:
        raise ServiceError("Invalid conclusion status.")
    if status == ConclusionStatus.VERIFIED and not can(actor, "conclusion.verify", review, rr):
        raise PermissionDenied("conclusion.verify")
    if status in (ConclusionStatus.SECURITY_GAP, ConclusionStatus.EVIDENCE_REQUIRED, ConclusionStatus.NOT_APPLICABLE) \
            and not notes.strip():
        raise ServiceError("Notes are required for this conclusion.")
    conclusion = Conclusion.objects.create(review_requirement=rr, status=status, notes=notes, decided_by=actor)
    ReviewFlag.objects.filter(review_requirement=rr, resolved_at__isnull=True).update(resolved_at=timezone.now())
    record("conclusion.recorded", actor=actor, review=review, obj=conclusion, request=request,
           detail={"requirement": rr.control.code, "status": status})
    return conclusion
