# apps/payments/serializers.py

from rest_framework import serializers
from .models import CreditID, CreditTransaction, CreditPayment
from document.models import SalesOrder


# =========================
# Credit Customer
# =========================

class CreditIDSerializer(serializers.ModelSerializer):

    class Meta:
        model = CreditID
        fields = [
            'credit_id',
            'customer_name',
        ]
        read_only_fields = ['credit_id']


# =========================
# Credit Transaction
# =========================

class CreditTransactionSerializer(serializers.ModelSerializer):
    customer_name = serializers.ReadOnlyField(source='customer.customer_name')

    class Meta:
        model = CreditTransaction
        fields = [
            'id',
            'customer',
            'customer_name',
            'sales',
            'amount',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']


# =========================
# Credit Payment
# =========================

class CreditPaymentSerializer(serializers.ModelSerializer):
    customer_name = serializers.ReadOnlyField(source='customer.customer_name')

    class Meta:
        model = CreditPayment
        fields = [
            'id',
            'customer',
            'customer_name',
            'amount',
            'paid_at',
        ]
        read_only_fields = ['id', 'paid_at']


# =========================
# Create Credit Payment
# =========================

class CreditPaymentCreateSerializer(serializers.ModelSerializer):

    class Meta:
        model = CreditPayment
        fields = [
            'customer',
            'amount',
        ]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "Amount must be greater than zero"
            )
        return value


# =========================
# Credit Customer Details
# =========================

class CreditCustomerDetailSerializer(serializers.ModelSerializer):
    transactions = CreditTransactionSerializer(many=True, read_only=True)
    credit_payments = CreditPaymentSerializer(many=True, read_only=True)

    total_credit = serializers.SerializerMethodField()
    total_paid = serializers.SerializerMethodField()
    balance = serializers.SerializerMethodField()

    class Meta:
        model = CreditID
        fields = [
            'credit_id',
            'customer_name',
            'transactions',
            'credit_payments',
            'total_credit',
            'total_paid',
            'balance',
        ]

    def get_total_credit(self, obj):
        return sum(
            transaction.amount
            for transaction in obj.transactions.all()
        )

    def get_total_paid(self, obj):
        return sum(
            payment.amount
            for payment in obj.credit_payments.all()
        )

    def get_balance(self, obj):
        total_credit = self.get_total_credit(obj)
        total_paid = self.get_total_paid(obj)

        return total_credit - total_paid
