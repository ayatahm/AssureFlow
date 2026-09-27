from django.urls import include, path
from django.views.generic import RedirectView

from apps.administration.admin_site import site as admin_site
from apps.core import views as core_views

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="core:dashboard", permanent=False)),
    path("account/", include("apps.accounts.urls")),
    path("", include(([path("dashboard/", core_views.dashboard, name="dashboard")], "core"))),
    path("reviews/", include("apps.reviews.urls")),
    path("", include("apps.assurance.urls")),
    path("", include("apps.reporting.urls")),
    path("administration/", include("apps.administration.urls")),
    path("admin/", admin_site.urls),
]

handler403 = "apps.core.views.error_403"
handler404 = "apps.core.views.error_404"
handler500 = "apps.core.views.error_500"
