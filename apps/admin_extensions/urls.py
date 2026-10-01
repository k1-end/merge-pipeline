from django.urls import path

from apps.admin_extensions import views

app_name = "admin_extensions"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("queue-status/", views.queue_status, name="queue_status"),
    path("sync/", views.sync_merge_requests, name="sync_merge_requests"),
    path(
        "merge-requests/review-all/",
        views.run_all_unreviewed_merge_requests,
        name="run_all_unreviewed_merge_requests",
    ),
    path("merge-requests/<int:pk>/", views.merge_request_detail, name="merge_request_detail"),
    path("merge-requests/<int:pk>/review/", views.run_merge_request_review, name="run_merge_request_review"),
    path(
        "merge-requests/<int:pk>/comments/",
        views.post_merge_request_comments,
        name="post_merge_request_comments",
    ),
    path("projects/", views.projects, name="projects"),
    path("projects/<int:pk>/clone-cache/", views.clone_project_cache, name="clone_project_cache"),
    path("merge-requests/", views.merge_requests, name="merge_requests"),
    path("reviews/", views.reviews, name="reviews"),
    path("reviews/<int:pk>/", views.review_detail, name="review_detail"),
    path(
        "reviews/<int:pk>/execution-log/",
        views.review_execution_log,
        name="review_execution_log",
    ),
    path("sources/", views.sources, name="sources"),
    path("sources/add/", views.source_create, name="source_create"),
    path("sources/<int:pk>/edit/", views.source_edit, name="source_edit"),
    path("providers/", views.providers, name="providers"),
    path("providers/add/", views.provider_create, name="provider_create"),
    path("providers/<int:pk>/edit/", views.provider_edit, name="provider_edit"),
]
