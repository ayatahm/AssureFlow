from django.urls import path

from . import views

app_name = "reviews"

urlpatterns = [
    path("", views.review_list, name="list"),
    path("new/", views.review_new, name="new"),
    path("<int:pk>/", views.review_detail, name="detail"),
    path("<int:pk>/intake/", views.intake_edit, name="intake"),
    path("<int:pk>/triage/", views.triage_view, name="triage"),
    path("<int:pk>/generate/", views.generate, name="generate"),
    path("<int:pk>/assessment/", views.assessment_view, name="assessment"),
    path("<int:pk>/questions/<int:question_pk>/why/", views.question_why, name="why"),
    path("<int:pk>/submit/", views.submit, name="submit"),
    path("<int:pk>/return-to-draft/", views.return_to_draft, name="return_to_draft"),
    path("<int:pk>/withdraw/", views.withdraw, name="withdraw"),
    path("<int:pk>/return-to-requester/", views.return_to_requester, name="return_to_requester"),
    path("<int:pk>/workbench/", views.workbench, name="workbench"),
    path("<int:pk>/controls/<int:rr_pk>/conclude/", views.requirement_detail, name="requirement"),
    path("<int:pk>/technical/", views.technical_scope, name="technical"),
    path("<int:pk>/technical/add/", views.technical_add, name="technical_add"),
    path("<int:pk>/technical/<int:item_pk>/", views.technical_item, name="technical_item"),
    path("<int:pk>/scope/confirm/", views.scope_confirm, name="scope_confirm"),
    path("<int:pk>/audit/", views.audit_log, name="audit"),
]
