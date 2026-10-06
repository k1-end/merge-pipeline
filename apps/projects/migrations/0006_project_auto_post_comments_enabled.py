from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("projects", "0005_mergerequest_source_sha")]

    operations = [
        migrations.AddField(
            model_name="project",
            name="auto_post_comments_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Publish findings after successful manual or automatic AI reviews.",
                verbose_name="Automatically post AI review comments",
            ),
        ),
    ]
