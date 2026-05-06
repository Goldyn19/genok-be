from django.db import models
from django.contrib.auth import get_user_model
from product.models import Stock
import uuid
from decimal import Decimal


User = get_user_model()


class Cart(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    customer_name = models.CharField(max_length=50)

    is_paid = models.BooleanField(default=False)
    is_checked_out = models.BooleanField(default=False)

    checked_out_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class CartItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Stock, on_delete=models.CASCADE)

    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    date_added = models.DateTimeField(auto_now_add=True)

    @property
    def price(self):
        return self.unit_price

    @property
    def part_number(self):
        return self.product.part_number

    @property
    def part_name(self):
        return self.product.name

    @property
    def total(self):
        return self.unit_price * self.quantity

    def increase_quantity(self, quantity):
        self.quantity += int(quantity)
        self.save()

# Create your models here.
