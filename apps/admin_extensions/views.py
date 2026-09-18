from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.admin_extensions.forms import ProviderForm, SourceForm
from apps.projects.models import MergeRequest, Project
from apps.reviews.models import AIProvider, Review, ReviewComment
from apps.sources.models import GitSource


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


@staff_member_required(login_url="admin:login")
def sources(request: HttpRequest) -> HttpResponse:
    """Render configured git sources."""
    source_list = GitSource.objects.order_by("name")
    return render(request, "admin_extensions/sources.html", {"sources": source_list})


@staff_member_required(login_url="admin:login")
def source_create(request: HttpRequest) -> HttpResponse:
    """Create a git source and optional encrypted credentials."""
    form = SourceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Git source added successfully.")
        return redirect("admin_extensions:sources")
    return render(request, "admin_extensions/source_form.html", {"form": form, "title": "Add git source"})


@staff_member_required(login_url="admin:login")
def source_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Update a git source and optional encrypted credentials."""
    source = get_object_or_404(GitSource, pk=pk)
    form = SourceForm(request.POST or None, instance=source)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Git source updated successfully.")
        return redirect("admin_extensions:sources")
    return render(request, "admin_extensions/source_form.html", {"form": form, "title": "Edit git source"})


@staff_member_required(login_url="admin:login")
def providers(request: HttpRequest) -> HttpResponse:
    """Render configured AI providers."""
    provider_list = AIProvider.objects.order_by("name")
    return render(request, "admin_extensions/providers.html", {"providers": provider_list})


@staff_member_required(login_url="admin:login")
def provider_create(request: HttpRequest) -> HttpResponse:
    """Create an AI provider with an optional encrypted API key."""
    form = ProviderForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "AI provider added successfully.")
        return redirect("admin_extensions:providers")
    return render(request, "admin_extensions/provider_form.html", {"form": form, "title": "Add AI provider"})


@staff_member_required(login_url="admin:login")
def provider_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Update an AI provider and optional encrypted API key."""
    provider = get_object_or_404(AIProvider, pk=pk)
    form = ProviderForm(request.POST or None, instance=provider)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "AI provider updated successfully.")
        return redirect("admin_extensions:providers")
    return render(request, "admin_extensions/provider_form.html", {"form": form, "title": "Edit AI provider"})
