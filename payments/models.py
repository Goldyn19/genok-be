from django.db import models
import uuid
from document.models import SalesOrder


class CreditID(models.Model):
    customer_name = models.CharField(max_length=125)
    credit_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    def __str__(self):
        return f" {self.customer_name} "


class CreditTransaction(models.Model):
    customer = models.ForeignKey(CreditID, on_delete=models.CASCADE, related_name='transactions')
    sales = models.ForeignKey(SalesOrder, on_delete=models.CASCADE)
    amount = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)


class CreditPayment(models.Model):
    customer = models.ForeignKey(CreditID, on_delete=models.CASCADE, related_name='credit_payments')
    amount = models.IntegerField()
    paid_at = models.DateTimeField(auto_now_add=True)
# Create your models here.
