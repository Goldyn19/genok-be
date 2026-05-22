from django.shortcuts import render

# apps/payments/views.py

from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import CreditID, CreditPayment, CreditTransaction
from .serializers import (
    CreditIDSerializer,
    CreditCustomerDetailSerializer,
    CreditPaymentSerializer,
    CreditPaymentCreateSerializer,
    CreditTransactionSerializer,
)


# =========================
# Credit Customers
# =========================

class CreditCustomerListCreateView(generics.ListCreateAPIView):
    queryset = CreditID.objects.all().order_by('customer_name')
    serializer_class = CreditIDSerializer
    permission_classes = [IsAuthenticated]


class CreditCustomerDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = CreditID.objects.all()
    serializer_class = CreditCustomerDetailSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'credit_id'


# =========================
# Credit Transactions
# =========================

class CreditTransactionListView(generics.ListAPIView):
    serializer_class = CreditTransactionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        customer_id = self.kwargs.get('credit_id')

        return CreditTransaction.objects.filter(
            customer__credit_id=customer_id
        ).select_related(
            'customer',
            'sales'
        ).order_by('-created_at')


# =========================
# Credit Payments
# =========================

class CreditPaymentListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        customer_id = self.kwargs.get('credit_id')

        return CreditPayment.objects.filter(
            customer__credit_id=customer_id
        ).select_related('customer').order_by('-paid_at')

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return CreditPaymentCreateSerializer

        return CreditPaymentSerializer

    def perform_create(self, serializer):
        customer_id = self.kwargs.get('credit_id')

        customer = CreditID.objects.filter(
            credit_id=customer_id
        ).first()

        serializer.save(customer=customer)


class CreditPaymentDetailView(generics.RetrieveDestroyAPIView):
    queryset = CreditPayment.objects.all()
    serializer_class = CreditPaymentSerializer
    permission_classes = [IsAuthenticated]
# Create your views here.
