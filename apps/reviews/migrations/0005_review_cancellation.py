from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reviews", "0004_reviewexecutionlogchunk")]

    operations = [
        migrations.AddField(
            model_name="review",
            name="celery_task_id",
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True),
        ),
        migrations.AlterField(
            model_name="review",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("processing", "Processing"),
                    ("completed", "Completed"),
                    ("failed", "Failed"),
                    ("cancelled", "Cancelled"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
