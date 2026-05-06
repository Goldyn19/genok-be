# apps/roles/permissions.py
from rest_framework import permissions


class IsAdminOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow admins to edit.
    Read-only access for everyone else.
    """

    def has_permission(self, request, view):
        # Read-only for all authenticated users
        if request.method in permissions.SAFE_METHODS:
            return request.user and request.user.is_authenticated

        # Write operations require staff status
        return request.user and request.user.is_staff


class IsRoleManager(permissions.BasePermission):
    """
    Permission to manage roles.
    Only staff users or users with specific permission can manage roles.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        # Staff users have full access
        if request.user.is_staff:
            return True

        # Check for specific permission
        return request.user.has_perm('roles.manage_roles')

    def has_object_permission(self, request, view, obj):
        # Same as has_permission for object-level checks
        return self.has_permission(request, view)


class IsRoleAssignmentOwner(permissions.BasePermission):
    """
    Permission to view role assignments.
    Users can view their own assignments, staff can view all.
    """

    def has_object_permission(self, request, view, obj):
        # Staff can access any assignment
        if request.user.is_staff:
            return True

        # Users can access their own assignments
        if hasattr(obj, 'user'):
            return obj.user == request.user

        return False


class IsSuperuserOnly(permissions.BasePermission):
    """
    Strict permission: Only superusers can access.
    Use this for dangerous operations like direct group manipulation.
    """

    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated and request.user.is_superuser

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)
