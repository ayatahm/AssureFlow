from django.urls import path

from . import views

app_name = "reporting"

urlpatterns = [
    path("reviews/<int:pk>/report/", views.report, name="report"),
]
