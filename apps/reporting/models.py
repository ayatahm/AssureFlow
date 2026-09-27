from django.conf import settings
from django.db import models

from apps.audit.models import AppendOnlyModel

SERIALIZER_VERSION = "af-json-v1"


class ReportSnapshot(AppendOnlyModel):
    """Immutable issued report data (spec 18, 21.5)."""

    review = models.ForeignKey("reviews.Review", on_delete=models.PROTECT, related_name="snapshots")
    revision = models.PositiveIntegerField()
    serializer_version = models.CharField(max_length=32, default=SERIALIZER_VERSION)
    data = models.JSONField()
    sha256 = models.CharField(max_length=64)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["review", "revision"]
        constraints = [models.UniqueConstraint(fields=["review", "revision"], name="uniq_snapshot_revision")]

    def __str__(self):
        return f"{self.review_id} r{self.revision}"
