from rest_framework.permissions import BasePermission


class IsLocationManager(BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.groups.filter(name='Location Manager').exists()


class IsManagerLevel1(BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.groups.filter(name='Manager Level1').exists()
