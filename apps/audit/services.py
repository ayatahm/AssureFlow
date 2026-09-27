from django.db import transaction

from .models import AuditEvent

# Keys that must never be written into an audit payload.
FORBIDDEN_DETAIL_KEYS = {"password", "code", "token", "otp", "secret"}


def client_ip(request):
    if request is None:
        return None
    return request.META.get("REMOTE_ADDR") or None


def record(action, *, actor=None, review=None, obj=None, detail=None, request=None):
    """Write an AuditEvent. Callers performing a state change call this inside
    the same ``transaction.atomic`` block as the change (spec rule 7)."""
    detail = dict(detail or {})
    bad = FORBIDDEN_DETAIL_KEYS.intersection(k.lower() for k in detail)
    if bad:
        raise ValueError(f"Audit detail must not contain credential fields: {sorted(bad)}")
    if actor is not None and not getattr(actor, "pk", None):
        actor = None
    with transaction.atomic():
        return AuditEvent.objects.create(
            action=action,
            actor=actor,
            review=review,
            object_type=type(obj).__name__ if obj is not None else "",
            object_id=str(obj.pk) if obj is not None and obj.pk is not None else "",
            detail=detail,
            ip_address=client_ip(request),
        )
