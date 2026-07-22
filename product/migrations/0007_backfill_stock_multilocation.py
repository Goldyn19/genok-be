from django.db import migrations


def backfill_top_level_location(apps, schema_editor):
    Stock = apps.get_model('product', 'Stock')
    for stock in Stock.objects.exclude(location__isnull=True).select_related('location'):
        root = stock.location
        while root.parent_id:
            root = root.parent
        stock.top_level_location = root
        stock.save(update_fields=['top_level_location'])
        stock.locations.add(stock.location)


def reverse_backfill(apps, schema_editor):
    Stock = apps.get_model('product', 'Stock')
    for stock in Stock.objects.exclude(top_level_location__isnull=True):
        stock.top_level_location = None
        stock.save(update_fields=['top_level_location'])
        stock.locations.clear()


class Migration(migrations.Migration):

    dependencies = [
        ('product', '0006_stock_multilocation'),
    ]

    operations = [
        migrations.RunPython(
            backfill_top_level_location,
            reverse_backfill,
            atomic=True,
        ),
    ]
