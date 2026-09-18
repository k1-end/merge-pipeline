from django.urls import path

from apps.admin_extensions import views

app_name = "admin_extensions"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("projects/", views.projects, name="projects"),
    path("merge-requests/", views.merge_requests, name="merge_requests"),
    path("reviews/", views.reviews, name="reviews"),
]
