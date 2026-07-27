from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def backfill_sales_order_history(apps, schema_editor):
    SalesOrder = apps.get_model("document", "SalesOrder")

    for sales_order in SalesOrder.objects.select_related("cart__user").all().iterator():
        cart = getattr(sales_order, "cart", None)
        sale_time = getattr(cart, "checked_out_at", None) or sales_order.created_at
        sale_user_id = getattr(cart, "user_id", None)

        update_fields = []

        if sales_order.sold_at is None:
            sales_order.sold_at = sale_time
            update_fields.append("sold_at")

        if sales_order.sold_by_id is None and sale_user_id:
            sales_order.sold_by_id = sale_user_id
            update_fields.append("sold_by")

        if sales_order.entered_by_id is None and sale_user_id:
            sales_order.entered_by_id = sale_user_id
            update_fields.append("entered_by")

        if update_fields:
            sales_order.save(update_fields=update_fields)


def reverse_backfill_sales_order_history(apps, schema_editor):
    SalesOrder = apps.get_model("document", "SalesOrder")
    SalesOrder.objects.all().update(sold_by=None, entered_by=None)


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("document", "0009_purchasebook_stock_attributes"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="salesorder",
            options={
                "permissions": [("can_backdate_sale", "Can create backdated sales and assign salesperson")]
            },
        ),
        migrations.AddField(
            model_name="salesorder",
            name="sold_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="salesorder",
            name="sold_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sales_made",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="salesorder",
            name="entered_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sales_entered",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(backfill_sales_order_history, reverse_backfill_sales_order_history),
        migrations.AlterField(
            model_name="salesorder",
            name="sold_at",
            field=models.DateTimeField(db_index=True, default=django.utils.timezone.now),
        ),
    ]
