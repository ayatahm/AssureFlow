from django.core.exceptions import PermissionDenied as HttpForbidden
from django.shortcuts import render
from django.utils import timezone

from apps.assurance.services import completion as completion_svc
from apps.core.permissions import can
from apps.core.viewhelpers import load_review, primary_role, review_context
from apps.reviews.models import ReviewState

from .services import build_report_data, project_report, verify_snapshot


def report(request, pk):
    review = load_review(request, pk)
    role = primary_role(request.user, review)
    snapshot = review.snapshots.order_by("revision").first()
    if review.state == ReviewState.COMPLETED and snapshot is not None:
        if not can(request.user, "report.view", review):
            raise HttpForbidden("report.view")
        # Rendered from the stored snapshot only; viewing never mutates it.
        data = project_report(snapshot.data, role=role, user_id=request.user.pk)
        return render(request, "reporting/report.html", review_context(
            request, review, "report", data=data, snapshot=snapshot, draft=False,
            hash_ok=verify_snapshot(snapshot),
        ))
    if not can(request.user, "report.preview", review):
        return render(request, "reporting/report_unavailable.html", review_context(request, review, "report"))
    chk = completion_svc.evaluate_completion(review)
    data = None
    if chk.final_risk is not None and review.rule_set_id:
        # Draft preview: built live, clearly marked DRAFT, never stored or hashed.
        review.outcome = chk.outcome or ""
        data = build_report_data(review, chk, issued_at=timezone.now(), issued_by=request.user, revision=0)
    return render(request, "reporting/report.html", review_context(
        request, review, "report", data=data, snapshot=None, draft=True, chk=chk,
    ))
