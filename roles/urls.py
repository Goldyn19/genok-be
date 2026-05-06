# apps/roles/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'permissions', views.PermissionViewSet, basename='permission')
router.register(r'groups', views.GroupViewSet, basename='group')
router.register(r'roles', views.RoleViewSet, basename='role')
router.register(r'assignments', views.UserRoleAssignmentViewSet, basename='assignment')
router.register(r'users', views.UserViewSet, basename='user')

urlpatterns = [
    # Dashboard
    path('dashboard/', views.RoleDashboardView.as_view(), name='role-dashboard'),

    # Include router URLs
    path('', include(router.urls)),
]
