"""Root URL configuration."""

from django.contrib import admin
from django.urls import include, path

from apps.core.views import health_check

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "dashboard/",
        include(("apps.admin_extensions.urls", "admin_extensions"), namespace="admin_extensions"),
    ),
    path("health/", health_check, name="health_check"),
    # Webhook endpoints (Phase 3)
    path("webhooks/", include("apps.sources.urls")),
]
