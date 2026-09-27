from django.urls import path

from . import views

app_name = "administration"

urlpatterns = [
    path("review-assignments/", views.review_assignments, name="assignments"),
    path("account-events/", views.account_events, name="account_events"),
]
