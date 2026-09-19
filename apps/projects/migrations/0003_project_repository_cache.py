from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("projects", "0002_project_ai_provider")]

    operations = [
        migrations.AddField(
            model_name="project",
            name="cache_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="project",
            name="cache_status",
            field=models.CharField(
                choices=[
                    ("missing", "Missing"),
                    ("cloning", "Cloning"),
                    ("ready", "Ready"),
                    ("error", "Error"),
                ],
                default="missing",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="project",
            name="cache_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
