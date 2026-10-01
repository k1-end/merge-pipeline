from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reviews", "0002_aiprovider_opencode")]

    operations = [
        migrations.AddField(
            model_name="review",
            name="source_branch",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="review",
            name="target_branch",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="review",
            name="base_sha",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="review",
            name="head_sha",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="review",
            name="merge_base_sha",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
