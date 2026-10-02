from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("projects", "0004_alter_mergerequest_options_mrdiscussionsyncstate_and_more")]

    operations = [
        migrations.AddField(
            model_name="mergerequest",
            name="source_sha",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
