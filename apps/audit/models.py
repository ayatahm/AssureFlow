from django.conf import settings
from django.db import models


class ImmutableRecordError(Exception):
    pass


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ImmutableRecordError(f"{self.model.__name__} records are append-only.")

    def delete(self):
        raise ImmutableRecordError(f"{self.model.__name__} records are append-only.")

    def bulk_update(self, *args, **kwargs):
        raise ImmutableRecordError(f"{self.model.__name__} records are append-only.")


class AppendOnlyModel(models.Model):
    """Base for records that application code may create but never change or delete."""

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise ImmutableRecordError(f"{type(self).__name__} records cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError(f"{type(self).__name__} records cannot be deleted.")


class AuditEvent(AppendOnlyModel):
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    action = models.CharField(max_length=64, db_index=True)
    review = models.ForeignKey("reviews.Review", null=True, blank=True, on_delete=models.PROTECT, related_name="audit_events")
    object_type = models.CharField(max_length=64, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["timestamp", "pk"]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.action}"
