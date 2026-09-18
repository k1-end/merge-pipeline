from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.projects.models import MergeRequest, Project
from apps.reviews.models import Review, ReviewComment


@staff_member_required(login_url="admin:login")
def dashboard(request: HttpRequest) -> HttpResponse:
    """Render the operational overview for staff users."""
    context = {
        "active_projects": Project.objects.filter(is_active=True).count(),
        "open_merge_requests": MergeRequest.objects.filter(state=MergeRequest.OPENED).count(),
        "pending_reviews": Review.objects.filter(status=Review.PENDING).count(),
        "unresolved_comments": ReviewComment.objects.filter(is_resolved=False).count(),
        "recent_merge_requests": MergeRequest.objects.select_related("project").order_by(
            "-external_updated_at", "-id"
        )[:8],
        "recent_reviews": Review.objects.select_related("merge_request", "provider").order_by(
            "-created_at", "-id"
        )[:6],
    }
    return render(request, "admin_extensions/dashboard.html", context)


@staff_member_required(login_url="admin:login")
def projects(request: HttpRequest) -> HttpResponse:
    """Render monitored repositories in the panel."""
    project_list = Project.objects.select_related("source", "ai_provider").order_by("full_name")
    return render(request, "admin_extensions/projects.html", {"projects": project_list})


@staff_member_required(login_url="admin:login")
def merge_requests(request: HttpRequest) -> HttpResponse:
    """Render imported merge requests in the panel."""
    merge_request_list = MergeRequest.objects.select_related("project", "project__source").order_by(
        "-external_updated_at", "-id"
    )
    return render(request, "admin_extensions/merge_requests.html", {"merge_requests": merge_request_list})


@staff_member_required(login_url="admin:login")
def reviews(request: HttpRequest) -> HttpResponse:
    """Render recent AI reviews and their current status."""
    review_list = Review.objects.select_related("merge_request", "provider").order_by("-created_at", "-id")
    return render(request, "admin_extensions/reviews.html", {"reviews": review_list})
