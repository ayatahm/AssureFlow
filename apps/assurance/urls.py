from django.urls import path

from . import views

app_name = "assurance"

urlpatterns = [
    path("reviews/<int:pk>/evidence/", views.evidence_list, name="evidence"),
    path("reviews/<int:pk>/evidence/upload/", views.evidence_upload, name="evidence_upload"),
    path("evidence/<int:evidence_pk>/download/", views.evidence_download, name="evidence_download"),
    path("reviews/<int:pk>/findings/", views.findings_list, name="findings"),
    path("reviews/<int:pk>/findings/new/", views.finding_new, name="finding_new"),
    path("reviews/<int:pk>/findings/<int:finding_pk>/", views.finding_detail, name="finding"),
    path("actions/<int:action_pk>/", views.action_detail, name="action"),
    path("actions/<int:action_pk>/submit/", views.action_submit, name="action_submit"),
    path("actions/<int:action_pk>/close/", views.action_close, name="action_close"),
    path("actions/<int:action_pk>/reopen/", views.action_reopen, name="action_reopen"),
    path("reviews/<int:pk>/compliance/<int:rr_pk>/", views.compliance_decision, name="compliance"),
    path("reviews/<int:pk>/risk/", views.compliance_risk, name="risk"),
    path("reviews/<int:pk>/acceptance/", views.acceptance_view, name="acceptance"),
    path("acceptances/<int:acceptance_pk>/decide/", views.acceptance_decide, name="decide"),
    path("reviews/<int:pk>/complete/", views.complete, name="complete"),
    path("reviews/<int:pk>/controls/<int:rr_pk>/trace/", views.trace, name="trace"),
]
