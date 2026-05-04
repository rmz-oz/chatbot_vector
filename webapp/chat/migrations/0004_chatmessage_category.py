from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0003_chatmessage_feedback"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatmessage",
            name="category",
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
    ]
