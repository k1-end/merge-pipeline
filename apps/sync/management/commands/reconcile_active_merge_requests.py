"""Repair locally active merge requests whose remote state changed."""

from django.core.management.base import BaseCommand

from apps.projects.models import Project
from apps.sync.services import SyncService


class Command(BaseCommand):
    """Reconcile active merge requests for all or one project."""

    help = "Refresh local active merge requests that may have closed remotely."

    def add_arguments(self, parser) -> None:
        """Register optional project and dry-run filters."""
        parser.add_argument("--project", type=int, help="Only reconcile one project ID.")
        parser.add_argument("--dry-run", action="store_true", help="Report changes without writing them.")

    def handle(self, *args, **options) -> None:
        """Reconcile active requests and print per-project and total counts."""
        projects = Project.objects.filter(is_active=True).select_related("source", "source__credential")
        if options["project"]:
            projects = projects.filter(pk=options["project"])
        total = {"checked": 0, "updated": 0, "unchanged": 0, "failed": 0}
        for project in projects.iterator(chunk_size=100):
            counts = SyncService().reconcile_active_merge_requests(project, options["dry_run"])
            for key, value in counts.items():
                total[key] += value
            self.stdout.write(f"{project.full_name}: {counts}")
        self.stdout.write(f"Total: {total}")
