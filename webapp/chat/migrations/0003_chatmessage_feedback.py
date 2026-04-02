from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0002_knowledgeentry_embedding'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatmessage',
            name='feedback',
            field=models.CharField(
                blank=True,
                choices=[('up', 'up'), ('down', 'down')],
                max_length=4,
                null=True,
            ),
        ),
    ]
