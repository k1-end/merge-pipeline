from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Prefetch
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.admin_extensions.forms import ProviderForm, SourceForm
from apps.projects.models import MergeRequest, Project
from apps.reviews.models import AIProvider, Review, ReviewComment
from apps.reviews.repository_cache import RepositoryCache
from apps.reviews.services import (
    ReviewAlreadyRunningError,
    ReviewConfigurationError,
    ReviewQueueService,
)
from apps.sources.models import GitSource


@staff_member_required(login_url="admin:login")
def dashboard(request: HttpRequest) -> HttpResponse:
    """Render the operational overview for staff users."""
    recent_merge_requests = (
        MergeRequest.objects.select_related("project")
        .prefetch_related(
            Prefetch(
                "reviews",
                queryset=Review.objects.select_related("provider").order_by("-created_at", "-id"),
                to_attr="recent_reviews",
            )
        )
        .order_by("-external_updated_at", "-id")[:8]
    )
    for merge_request in recent_merge_requests:
        merge_request.latest_review = (
            merge_request.recent_reviews[0] if merge_request.recent_reviews else None
        )
    context = {
        "active_projects": Project.objects.filter(is_active=True).count(),
        "open_merge_requests": MergeRequest.objects.filter(state=MergeRequest.OPENED).count(),
        "pending_reviews": Review.objects.filter(status=Review.PENDING).count(),
        "unresolved_comments": ReviewComment.objects.filter(is_resolved=False).count(),
        "recent_merge_requests": recent_merge_requests,
        "recent_reviews": Review.objects.select_related("merge_request", "provider").order_by(
            "-created_at", "-id"
        )[:6],
    }
    return render(request, "admin_extensions/dashboard.html", context)


@staff_member_required(login_url="admin:login")
def sync_merge_requests(request: HttpRequest) -> HttpResponse:
    """Queue synchronization for all active, sync-enabled projects."""
    if request.method != "POST":
        return HttpResponse(status=405)

    from apps.sync.tasks import sync_all_projects

    try:
        sync_all_projects.delay()
    except Exception:  # noqa: BLE001 - broker errors must be visible to the operator
        messages.error(request, "Merge-request sync could not be queued.")
    else:
        messages.success(request, "Merge-request sync queued for active projects.")
    return redirect("admin_extensions:dashboard")


@staff_member_required(login_url="admin:login")
def run_merge_request_review(request: HttpRequest, pk: int) -> HttpResponse:
    """Queue a manual AI review for one merge request from the dashboard."""
    if request.method != "POST":
        return HttpResponse(status=405)
    merge_request = get_object_or_404(
        MergeRequest.objects.select_related("project", "project__source", "project__ai_provider"), pk=pk
    )
    try:
        ReviewQueueService.queue_manual_review(merge_request)
    except ReviewAlreadyRunningError as exc:
        messages.warning(request, str(exc))
    except ReviewConfigurationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"AI review queued for !{merge_request.external_iid}.")
    return redirect("admin_extensions:dashboard")


@staff_member_required(login_url="admin:login")
def post_merge_request_comments(request: HttpRequest, pk: int) -> HttpResponse:
    """Queue publication of pending comments from the latest completed review."""
    if request.method != "POST":
        return HttpResponse(status=405)
    merge_request = get_object_or_404(MergeRequest, pk=pk)
    review = (
        Review.objects.filter(merge_request=merge_request, status=Review.COMPLETED)
        .prefetch_related("comments")
        .order_by("-created_at", "-id")
        .first()
    )
    if review is None:
        messages.warning(request, "There is no completed AI review to publish.")
        return redirect("admin_extensions:merge_request_detail", pk=pk)
    pending_count = review.comments.filter(external_comment_id="").count()
    if not pending_count:
        messages.info(request, "All comments from the latest AI review are already published.")
        return redirect("admin_extensions:merge_request_detail", pk=pk)

    from apps.reviews.tasks import post_review_comments

    post_review_comments.delay(review.pk)
    messages.success(request, f"Queued {pending_count} comment(s) for publication.")
    return redirect("admin_extensions:merge_request_detail", pk=pk)


@staff_member_required(login_url="admin:login")
def run_all_unreviewed_merge_requests(request: HttpRequest) -> HttpResponse:
    """Queue reviews for all opened merge requests without review history."""
    if request.method != "POST":
        return HttpResponse(status=405)

    selected_project = request.POST.get("project", "")
    selected_author = request.POST.get("author", "").strip()
    selected_name = request.POST.get("name", "").strip()
    merge_requests = (
        MergeRequest.objects.filter(state=MergeRequest.OPENED)
        .annotate(has_review=Exists(Review.objects.filter(merge_request=OuterRef("pk"))))
        .filter(has_review=False)
    )
    if selected_project.isdigit():
        merge_requests = merge_requests.filter(project_id=selected_project)
    elif selected_project:
        merge_requests = merge_requests.none()
    if selected_author:
        merge_requests = merge_requests.filter(author_username__icontains=selected_author)
    if selected_name:
        merge_requests = merge_requests.filter(title__icontains=selected_name)

    queued = 0
    skipped = 0
    for merge_request in merge_requests.select_related("project", "project__source", "project__ai_provider"):
        try:
            ReviewQueueService.queue_manual_review(merge_request)
        except (ReviewAlreadyRunningError, ReviewConfigurationError) as exc:
            skipped += 1
            messages.warning(request, f"!{merge_request.external_iid} was skipped: {exc}")
        else:
            queued += 1

    if skipped:
        messages.warning(request, f"Queued {queued} AI review(s); skipped {skipped}.")
    else:
        messages.success(request, f"Queued {queued} AI review(s).")

    query_params = {
        key: value
        for key, value in (
            ("project", selected_project),
            ("author", selected_author),
            ("name", selected_name),
        )
        if value
    }
    redirect_url = reverse("admin_extensions:merge_requests")
    if query_params:
        redirect_url += "?" + urlencode(query_params)
    return redirect(redirect_url)


@staff_member_required(login_url="admin:login")
def merge_request_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Render the operational detail view for one merge request."""
    merge_request = get_object_or_404(
        MergeRequest.objects.select_related(
            "project", "project__source", "project__ai_provider"
        ).prefetch_related(
            "changes",
            Prefetch(
                "reviews",
                queryset=Review.objects.select_related("provider")
                .prefetch_related("comments")
                .order_by("-created_at", "-id"),
                to_attr="review_history",
            ),
        ),
        pk=pk,
    )
    merge_request.latest_review = merge_request.review_history[0] if merge_request.review_history else None
    merge_request.latest_completed_review = next(
        (review for review in merge_request.review_history if review.status == Review.COMPLETED), None
    )
    changes = list(merge_request.changes.all())
    for change in changes:
        diff_lines = (change.diff or "").splitlines()
        change.additions = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        change.deletions = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
    all_comments = [comment for review in merge_request.review_history for comment in review.comments.all()]
    context = {
        "merge_request": merge_request,
        "review_history": merge_request.review_history,
        "unresolved_comments": [comment for comment in all_comments if not comment.is_resolved],
        "review_count": len(merge_request.review_history),
        "changes": changes,
        "change_count": len(changes),
        "addition_count": sum(change.additions for change in changes),
        "deletion_count": sum(change.deletions for change in changes),
        "pending_comment_count": (
            merge_request.latest_completed_review.comments.filter(external_comment_id="").count()
            if merge_request.latest_completed_review
            else 0
        ),
    }
    return render(request, "admin_extensions/merge_request_detail.html", context)


@staff_member_required(login_url="admin:login")
def projects(request: HttpRequest) -> HttpResponse:
    """Render monitored repositories in the panel."""
    project_list = Project.objects.select_related("source", "ai_provider").order_by("full_name")
    for project in project_list:
        project.cache_available = RepositoryCache(project).exists()  # type: ignore[attr-defined]
    return render(request, "admin_extensions/projects.html", {"projects": project_list})


@staff_member_required(login_url="admin:login")
def clone_project_cache(request: HttpRequest, pk: int) -> HttpResponse:
    """Queue creation of a project's persistent bare repository cache."""
    if request.method != "POST":
        return HttpResponse(status=405)
    project = get_object_or_404(Project.objects.select_related("source"), pk=pk)
    if not project.clone_url:
        project.cache_status = Project.CACHE_ERROR
        project.cache_error = "Project has no clone URL."
        project.save(update_fields=["cache_status", "cache_error", "updated_at"])
        messages.error(request, f"Repository cache for {project.full_name} cannot be cloned: no clone URL.")
        return redirect("admin_extensions:projects")
    cache = RepositoryCache(project)
    if cache.exists():
        project.cache_status = Project.CACHE_READY
        project.cache_error = ""
        project.save(update_fields=["cache_status", "cache_error", "updated_at"])
        messages.info(request, f"Repository cache for {project.full_name} already exists.")
        return redirect("admin_extensions:projects")
    if project.cache_status == Project.CACHE_CLONING:
        messages.warning(request, f"Repository cache for {project.full_name} is already being cloned.")
        return redirect("admin_extensions:projects")

    from apps.reviews.tasks import clone_repository_cache

    project.cache_status = Project.CACHE_CLONING
    project.cache_error = ""
    project.save(update_fields=["cache_status", "cache_error", "updated_at"])
    try:
        clone_repository_cache.delay(project.pk)
    except Exception as exc:  # noqa: BLE001 - broker errors must be visible to the operator
        project.cache_status = Project.CACHE_ERROR
        project.cache_error = str(exc)
        project.save(update_fields=["cache_status", "cache_error", "updated_at"])
        messages.error(request, "Repository cache could not be queued.")
    else:
        messages.success(request, f"Repository cache queued for {project.full_name}.")
    return redirect("admin_extensions:projects")


@staff_member_required(login_url="admin:login")
def merge_requests(request: HttpRequest) -> HttpResponse:
    """Render imported merge requests in the panel."""
    selected_project = request.GET.get("project", "")
    selected_author = request.GET.get("author", "").strip()
    selected_name = request.GET.get("name", "").strip()
    merge_request_list = (
        MergeRequest.objects.select_related("project", "project__source")
        .prefetch_related(
            Prefetch(
                "reviews",
                queryset=Review.objects.select_related("provider").order_by("-created_at", "-id"),
                to_attr="recent_reviews",
            )
        )
        .order_by("-external_updated_at", "-id")
    )
    if selected_project.isdigit():
        merge_request_list = merge_request_list.filter(project_id=selected_project)
    elif selected_project:
        merge_request_list = merge_request_list.none()
    if selected_author:
        merge_request_list = merge_request_list.filter(author_username__icontains=selected_author)
    if selected_name:
        merge_request_list = merge_request_list.filter(title__icontains=selected_name)

    projects = Project.objects.order_by("full_name")
    merge_requests_page = Paginator(merge_request_list, 25).get_page(request.GET.get("page"))
    for merge_request in merge_requests_page:
        merge_request.latest_review = (
            merge_request.recent_reviews[0] if merge_request.recent_reviews else None
        )
    return render(
        request,
        "admin_extensions/merge_requests.html",
        {
            "merge_requests": merge_requests_page,
            "merge_requests_page": merge_requests_page,
            "projects": projects,
            "selected_project": selected_project,
            "selected_author": selected_author,
            "selected_name": selected_name,
        },
    )


@staff_member_required(login_url="admin:login")
def reviews(request: HttpRequest) -> HttpResponse:
    """Render recent AI reviews and their current status."""
    review_list = Review.objects.select_related("merge_request", "provider").order_by("-created_at", "-id")
    reviews_page = Paginator(review_list, 25).get_page(request.GET.get("page"))
    return render(request, "admin_extensions/reviews.html", {"reviews": reviews_page, "reviews_page": reviews_page})


@staff_member_required(login_url="admin:login")
def review_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Render the execution details and findings for one AI review."""
    review = get_object_or_404(
        Review.objects.select_related(
            "provider",
            "merge_request",
            "merge_request__project",
            "merge_request__project__source",
        ).prefetch_related("comments"),
        pk=pk,
    )
    comments = list(review.comments.all())
    context = {
        "review": review,
        "comments": comments,
        "unresolved_comments": [comment for comment in comments if not comment.is_resolved],
        "finding_count": len(review.findings or []),
    }
    return render(request, "admin_extensions/review_detail.html", context)


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
