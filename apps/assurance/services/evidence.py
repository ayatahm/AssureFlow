"""Evidence upload/download (spec 9). Files live under EVIDENCE_ROOT, never URL-routed."""
import hashlib
import uuid
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction

from apps.assurance.models import Action, Evidence, EvidenceLink, EvidencePolicy
from apps.audit.services import record
from apps.core.errors import ServiceError
from apps.reviews.models import Review, ReviewRequirement, ReviewTechnicalItem
from apps.reviews.services.common import require

SIGNATURES = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


def sniff(head: bytes):
    for magic, content_type in SIGNATURES:
        if head.startswith(magic):
            return content_type
    return None


def evidence_root() -> Path:
    root = Path(settings.EVIDENCE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def evidence_path(evidence) -> Path:
    return evidence_root() / evidence.stored_name


def safe_display_name(name):
    name = Path(str(name).replace("\\", "/")).name.strip() or "evidence"
    return "".join(ch for ch in name if ch.isprintable())[:255]


def link_kwargs(target):
    if isinstance(target, ReviewRequirement):
        return {"review_requirement": target}
    if isinstance(target, ReviewTechnicalItem):
        return {"technical_item": target}
    if isinstance(target, Action):
        return {"action": target}
    raise ServiceError("Unsupported evidence target.")


def target_label(target):
    if isinstance(target, ReviewRequirement):
        return f"Requirement {target.control.code}"
    if isinstance(target, ReviewTechnicalItem):
        return f"Technical item {target.control.source_id}"
    return f"Remediation action {target.code} (finding {target.finding.code})"


def resolve_target(review, kind, pk):
    """Look the target up *within this review* so a changed hidden ID cannot cross reviews."""
    try:
        if kind == "requirement":
            return ReviewRequirement.objects.get(review=review, pk=pk)
        if kind == "technical":
            return ReviewTechnicalItem.objects.get(review=review, pk=pk)
        if kind == "action":
            return Action.objects.select_related("finding").get(finding__review=review, pk=pk)
    except (ReviewRequirement.DoesNotExist, ReviewTechnicalItem.DoesNotExist, Action.DoesNotExist, ValueError):
        pass
    raise ServiceError("Evidence target not found in this review.")


def upload_evidence(actor, review, target, uploaded_file, comment="", *, request=None):
    review = Review.objects.get(pk=review.pk)
    require(actor, "evidence.upload", review, target)
    policy = EvidencePolicy.current()
    if uploaded_file.size > policy.max_bytes:
        raise ServiceError(f"File exceeds the maximum size of {policy.max_bytes // (1024 * 1024)} MB.")

    head = uploaded_file.read(16)
    uploaded_file.seek(0)
    content_type = sniff(head)
    if content_type is None or content_type not in policy.allowed_types:
        raise ServiceError("File type not allowed. Upload a PDF, PNG or JPEG document.")

    stored_name = uuid.uuid4().hex
    path = evidence_root() / stored_name
    digest = hashlib.sha256()
    size = 0
    try:
        with open(path, "xb") as out:
            for chunk in uploaded_file.chunks():
                size += len(chunk)
                if size > policy.max_bytes:
                    raise ServiceError("File exceeds the maximum size.")
                digest.update(chunk)
                out.write(chunk)
        with transaction.atomic():
            review = Review.objects.select_for_update().get(pk=review.pk)
            require(actor, "evidence.upload", review, type(target).objects.get(pk=target.pk))
            evidence = Evidence.objects.create(
                review=review, original_name=safe_display_name(uploaded_file.name), stored_name=stored_name,
                content_type=content_type, size=size, sha256=digest.hexdigest(), comment=comment, uploaded_by=actor,
            )
            link = EvidenceLink.objects.create(evidence=evidence, linked_by=actor, **link_kwargs(target))
            record("evidence.uploaded", actor=actor, review=review, obj=evidence, request=request,
                   detail={"link_id": link.pk, "target": target_label(target), "sha256": evidence.sha256,
                           "size": size, "content_type": content_type})
        return link
    except BaseException:
        path.unlink(missing_ok=True)
        raise


@transaction.atomic
def link_existing(actor, review, evidence, target, *, request=None):
    review = Review.objects.select_for_update().get(pk=review.pk)
    if evidence.review_id != review.pk:
        raise ServiceError("Evidence belongs to another review.")
    require(actor, "evidence.upload", review, target)
    require(actor, "evidence.download", review, evidence)
    try:
        with transaction.atomic():
            link = EvidenceLink.objects.create(evidence=evidence, linked_by=actor, **link_kwargs(target))
    except IntegrityError as exc:
        raise ServiceError("That evidence is already linked to this item.") from exc
    record("evidence.linked", actor=actor, review=review, obj=evidence, request=request,
           detail={"link_id": link.pk, "target": target_label(target)})
    return link


def open_for_download(actor, evidence, *, request=None):
    review = evidence.review
    require(actor, "evidence.download", review, evidence)
    path = evidence_path(evidence)
    if not path.is_file():
        raise ServiceError("The stored file is missing. Run verify_evidence and contact the administrator.")
    record("evidence.downloaded", actor=actor, review=review, obj=evidence, request=request,
           detail={"sha256": evidence.sha256})
    return open(path, "rb")


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_all():
    """Yield (evidence, status) with status OK / MISSING / MODIFIED."""
    for ev in Evidence.objects.order_by("pk"):
        path = evidence_path(ev)
        if not path.is_file():
            yield ev, "MISSING"
        elif file_sha256(path) != ev.sha256:
            yield ev, "MODIFIED"
        else:
            yield ev, "OK"
