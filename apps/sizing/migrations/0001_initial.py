import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [("projects", "0005_mergerequest_source_sha")]

    operations = [
        migrations.CreateModel(
            name="SizeLabelPolicy",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("enabled", models.BooleanField(default=True)),
                ("xs_max", models.PositiveIntegerField(default=10)),
                ("s_max", models.PositiveIntegerField(default=50)),
                ("m_max", models.PositiveIntegerField(default=250)),
                ("l_max", models.PositiveIntegerField(default=1000)),
                ("revision", models.PositiveIntegerField(default=1)),
            ],
            options={"db_table": "size_label_policy"},
        ),
        migrations.CreateModel(
            name="MergeRequestSizeResult",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("source_sha", models.CharField(blank=True, max_length=64)),
                ("additions", models.PositiveIntegerField(default=0)),
                ("deletions", models.PositiveIntegerField(default=0)),
                ("changed_lines", models.PositiveIntegerField(default=0)),
                ("label", models.CharField(blank=True, max_length=32)),
                ("policy_revision", models.PositiveIntegerField(default=0)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("processing", "Processing"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("error_message", models.TextField(blank=True)),
                ("labeled_at", models.DateTimeField(blank=True, null=True)),
                (
                    "merge_request",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="size_result",
                        to="projects.mergerequest",
                    ),
                ),
            ],
            options={"db_table": "merge_request_size_results"},
        ),
        migrations.AddIndex(
            model_name="mergerequestsizeresult",
            index=models.Index(fields=["status", "policy_revision"], name="merge_reque_status_fcf274_idx"),
        ),
    ]
