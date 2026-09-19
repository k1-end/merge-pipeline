from django.urls import path

from apps.admin_extensions import views

app_name = "admin_extensions"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("merge-requests/<int:pk>/", views.merge_request_detail, name="merge_request_detail"),
    path("merge-requests/<int:pk>/review/", views.run_merge_request_review, name="run_merge_request_review"),
    path("projects/", views.projects, name="projects"),
    path("merge-requests/", views.merge_requests, name="merge_requests"),
    path("reviews/", views.reviews, name="reviews"),
    path("sources/", views.sources, name="sources"),
    path("sources/add/", views.source_create, name="source_create"),
    path("sources/<int:pk>/edit/", views.source_edit, name="source_edit"),
    path("providers/", views.providers, name="providers"),
    path("providers/add/", views.provider_create, name="provider_create"),
    path("providers/<int:pk>/edit/", views.provider_edit, name="provider_edit"),
]
