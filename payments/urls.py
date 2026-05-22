# apps/payments/urls.py

from django.urls import path

from .views import (
    CreditCustomerListCreateView,
    CreditCustomerDetailView,
    CreditTransactionListView,
    CreditPaymentListCreateView,
    CreditPaymentDetailView,
)

urlpatterns = [

    # Credit Customers
    path(
        'customers/',
        CreditCustomerListCreateView.as_view(),
        name='credit-customer-list-create'
    ),

    path(
        'customers/<uuid:credit_id>/',
        CreditCustomerDetailView.as_view(),
        name='credit-customer-detail'
    ),

    # Credit Transactions
    path(
        'customers/<uuid:credit_id>/transactions/',
        CreditTransactionListView.as_view(),
        name='credit-transactions'
    ),

    # Credit Payments
    path(
        'customers/<uuid:credit_id>/payments/',
        CreditPaymentListCreateView.as_view(),
        name='credit-payment-list-create'
    ),

    path(
        'payments/<int:pk>/',
        CreditPaymentDetailView.as_view(),
        name='credit-payment-detail'
    ),
]
