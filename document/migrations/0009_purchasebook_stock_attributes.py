from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("document", "0008_purchasebook_parent_stock"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchasebook",
            name="brand",
            field=models.CharField(blank=True, max_length=80, null=True),
        ),
        migrations.AddField(
            model_name="purchasebook",
            name="is_caterpillar",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="purchasebook",
            name="is_original",
            field=models.BooleanField(default=True),
        ),
    ]
