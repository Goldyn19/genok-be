# apps/purchases/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'purchases', views.PurchaseBookViewSet, basename='purchase')
router.register(r'approvals', views.PurchaseApprovalViewSet, basename='approval')
router.register(r'sales-items', views.SalesOrderItemViewSet, basename='sales-item')
router.register(r'sales-approvals', views.SalesApprovalViewSet, basename='sales-approval')
router.register(r'sales-returns', views.SalesReturnItemViewSet, basename='sales-return')

urlpatterns = [
    path('activity/', views.ActivityFeedView.as_view(), name='activity-feed'),
    path('', include(router.urls)),
    path('dashboard/', views.PurchaseDashboardView.as_view(), name='purchase-dashboard'),
]
