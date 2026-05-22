# apps/roles/views.py
from rest_framework import viewsets, generics, status, permissions as drf_permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.db import transaction, IntegrityError
from .models import Role, UserRoleAssignment
from .serializers import (
    PermissionSerializer, GroupSerializer, RoleSerializer, RoleDetailSerializer,
    UserRoleAssignmentSerializer, UserRoleAssignmentCreateSerializer,
    UserSerializer, UserDetailSerializer, GroupUserAddSerializer,
    GroupPermissionAddSerializer, RoleDashboardResponseSerializer
)
from .services import RoleService
from .permissions import IsAdminOrReadOnly, IsRoleManager, IsSuperuserOnly
from drf_yasg.utils import swagger_auto_schema

User = get_user_model()


# ==================== Permission Views ====================

class PermissionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing Django permissions.
    Permissions are read-only and managed through Django's admin.
    """
    queryset = Permission.objects.select_related('content_type').all()
    serializer_class = PermissionSerializer
    permission_classes = [drf_permissions.IsAuthenticated, IsAdminOrReadOnly]

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filter by app label
        app_label = self.request.query_params.get('app_label')
        if app_label:
            queryset = queryset.filter(content_type__app_label=app_label)

        # Filter by codename (partial match)
        codename = self.request.query_params.get('codename')
        if codename:
            queryset = queryset.filter(codename__icontains=codename)

        # Filter by name (partial match)
        name = self.request.query_params.get('name')
        if name:
            queryset = queryset.filter(name__icontains=name)

        return queryset

    @action(detail=False, methods=['get'], url_path='by-app')
    @swagger_auto_schema(operation_summary='List permissions grouped by app', tags=['Roles'])
    def by_app(self, request):
        """
        Get permissions grouped by app for easier UI display.
        """
        permissions = self.get_queryset()
        apps = {}

        for perm in permissions:
            app_label = perm.content_type.app_label
            if app_label not in apps:
                apps[app_label] = {
                    'app_label': app_label,
                    'app_name': perm.content_type.app_label.replace('_', ' ').title(),
                    'permissions': []
                }
            apps[app_label]['permissions'].append({
                'id': perm.id,
                'name': perm.name,
                'codename': perm.codename
            })

        return Response(list(apps.values()))

    @action(detail=False, methods=['get'], url_path='search')
    @swagger_auto_schema(operation_summary='Search permissions', tags=['Roles'])
    def search(self, request):
        """
        Search permissions by name or codename.
        """
        query = request.query_params.get('q', '')
        if not query:
            return Response([])

        permissions = self.get_queryset().filter(
            Q(name__icontains=query) |
            Q(codename__icontains=query)
        )[:50]  # Limit to 50 results

        serializer = self.get_serializer(permissions, many=True)
        return Response(serializer.data)


# ==================== Group Views ====================

class GroupViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Django groups.

    IMPORTANT: Direct group manipulation is RESTRICTED to superusers only.
    Normal role assignments should go through RoleViewSet and UserRoleAssignmentViewSet.
    This endpoint is for debugging and emergency access only.
    """
    queryset = Group.objects.prefetch_related('permissions').all()
    permission_classes = [drf_permissions.IsAuthenticated]

    def get_serializer_class(self):
        return GroupSerializer

    def get_permissions(self):
        """
        Restrict all write operations to superusers only.
        """
        if self.action in [
            'create', 'update', 'partial_update', 'destroy',
            'add_users', 'remove_users', 'add_permissions', 'remove_permissions'
        ]:
            return [drf_permissions.IsAuthenticated(), IsSuperuserOnly()]
        return [drf_permissions.IsAuthenticated(), IsAdminOrReadOnly()]

    @swagger_auto_schema(
        operation_summary='Create group',
        operation_description='RESTRICTED: Superusers only. For normal access control, use role assignments instead.',
        request_body=GroupSerializer,
        responses={201: GroupSerializer},
        tags=['Roles'],
    )
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_summary='Update group',
        operation_description='RESTRICTED: Superusers only. Direct group updates bypass the role system.',
        request_body=GroupSerializer,
        responses={200: GroupSerializer},
        tags=['Roles'],
    )
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_summary='Patch group',
        operation_description='RESTRICTED: Superusers only. Direct group updates bypass the role system.',
        request_body=GroupSerializer,
        responses={200: GroupSerializer},
        tags=['Roles'],
    )
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_summary='Delete group',
        operation_description='RESTRICTED: Superusers only. Direct group deletion can break role mappings.',
        responses={204: 'No Content'},
        tags=['Roles'],
    )
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)
    @action(detail=True, methods=['post'], url_path='add-users')
    @swagger_auto_schema(
        operation_summary='Add users to group',
        request_body=GroupUserAddSerializer,
        operation_description=(
            'RESTRICTED: Superusers only. Direct group assignment bypasses the role system. '
            'Use RoleViewSet.assign_users() for normal role assignments.'
        ),
        tags=['Roles'],
    )
    def add_users(self, request, pk=None):
        """
        Add users to a group.

        RESTRICTED: Superusers only.
        Use RoleViewSet.assign_users() for normal role assignments.
        """
        group = self.get_object()

        if not request.user.is_superuser:
            return Response(
                {
                    'error': 'Direct group assignment is not allowed',
                    'message': 'Use role assignments instead of direct group manipulation',
                    'alternative': 'POST /roles/roles/{role_id}/assign-users/'
                },
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = GroupUserAddSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        users = serializer.validated_data['user_ids']

        with transaction.atomic():
            group.user_set.add(*users)

        for user in users:
            RoleService.invalidate_user_cache(user.id)

        return Response({
            'warning': 'Direct group assignment bypasses role system',
            'message': f'Added {len(users)} users to group "{group.name}"',
            'user_ids': [user.id for user in users],
            'note': 'Consider using role assignments for proper audit trail'
        })

    @action(detail=True, methods=['post'], url_path='remove-users')
    @swagger_auto_schema(
        operation_summary='Remove users from group',
        operation_description=(
            'RESTRICTED: Superusers only. Direct group manipulation bypasses the role system. '
            'Use role deactivation for proper audit trail.'
        ),
        request_body=GroupUserAddSerializer,
        tags=['Roles'],
    )
    def remove_users(self, request, pk=None):
        """
        Remove users from a group.

        RESTRICTED: Superusers only.
        Use RoleViewSet to properly manage role assignments.
        """
        group = self.get_object()

        if not request.user.is_superuser:
            return Response(
                {
                    'error': 'Direct group manipulation is not allowed',
                    'message': 'Use role deactivation instead',
                    'alternative': 'POST /roles/assignments/{id}/deactivate/'
                },
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = GroupUserAddSerializer(data=request.data)

        serializer.is_valid(raise_exception=True)


        users = serializer.validated_data['user_ids']

        with transaction.atomic():
            group.user_set.remove(*users)

        # Clear role caches for affected users
        for user in users:
            RoleService.invalidate_user_cache(user.id)

        return Response({
            'warning': 'Direct group manipulation bypasses role system',
            'message': f'Removed {len(users)} users from group "{group.name}"',
            'user_ids': [user.id for user in users]
        })

    @action(detail=True, methods=['post'], url_path='add-permissions')
    @swagger_auto_schema(
        operation_summary='Add permissions to group',
        operation_description=(
            'RESTRICTED: Superusers only. Permissions should normally be managed through roles. '
            'This endpoint is for emergency/debug usage only.'
        ),
        request_body=GroupPermissionAddSerializer,
        tags=['Roles'],
    )
    def add_permissions(self, request, pk=None):
        """
        Add permissions to a group.

        RESTRICTED: Superusers only.
        Permissions should be managed through role templates.
        """
        group = self.get_object()

        if not request.user.is_superuser:
            return Response(
                {
                    'error': 'Direct permission assignment is not allowed',
                    'message': 'Manage permissions through role templates instead',
                    'alternative': 'Update role.required_group permissions'
                },
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = GroupPermissionAddSerializer(data=request.data)

        serializer.is_valid(raise_exception=True)


        permission_ids = serializer.validated_data['permission_ids']
        permissions = Permission.objects.filter(id__in=permission_ids)

        group.permissions.add(*permissions)

        return Response({
            'warning': 'Direct permission assignment bypasses role system',
            'message': f'Added {len(permissions)} permissions to group "{group.name}"',
            'permission_ids': permission_ids
        })

    @action(detail=True, methods=['post'], url_path='remove-permissions')
    @swagger_auto_schema(
        operation_summary='Remove permissions from group',
        operation_description='RESTRICTED: Superusers only.',
        request_body=GroupPermissionAddSerializer,
        tags=['Roles'],
    )
    def remove_permissions(self, request, pk=None):
        """
        Remove permissions from a group.

        RESTRICTED: Superusers only.
        """
        group = self.get_object()

        if not request.user.is_superuser:
            return Response(
                {'error': 'Direct permission manipulation not allowed'},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = GroupPermissionAddSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        permission_ids = serializer.validated_data['permission_ids']
        permissions = Permission.objects.filter(id__in=permission_ids)

        group.permissions.remove(*permissions)

        return Response({
            'message': f'Removed {len(permissions)} permissions from group "{group.name}"',
            'permission_ids': permission_ids
        })

    @action(detail=True, methods=['get'], url_path='users')
    @swagger_auto_schema(operation_summary='Get group users', operation_description='Get all users in a group (read-only).', tags=['Roles'])
    def users(self, request, pk=None):
        """
        Get all users in a group (read-only).
        """
        group = self.get_object()
        users = group.user_set.all()
        serializer = UserSerializer(users, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='permissions')
    @swagger_auto_schema(operation_summary='Get group permissions', operation_description='Get all permissions of a group (read-only).', tags=['Roles'])
    def permissions(self, request, pk=None):
        """
        Get all permissions of a group (read-only).
        """
        group = self.get_object()
        permissions = group.permissions.all()
        serializer = PermissionSerializer(permissions, many=True)
        return Response(serializer.data)


# ==================== Role Views ====================

class RoleViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing business roles.
    """
    queryset = Role.objects.select_related('required_group').prefetch_related('assignments')
    permission_classes = [drf_permissions.IsAuthenticated, IsRoleManager]

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return RoleDetailSerializer
        return RoleSerializer

    def perform_create(self, serializer):
        """Set created_by on role creation."""
        serializer.save(created_by=self.request.user)

    @swagger_auto_schema(operation_summary='Create role', tags=['Roles'])
    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except IntegrityError:
            return Response({'error': 'Role already exists'}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='assign-users')
    @swagger_auto_schema(
        operation_summary='Assign users to role',
        request_body=UserRoleAssignmentCreateSerializer,
        tags=['Roles'],
    )
    def assign_users(self, request, pk=None):
        """
        Assign users to this role (bulk assignment).
        """
        role = self.get_object()
        serializer = UserRoleAssignmentCreateSerializer(
            data=request.data,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)

        result = serializer.save()

        return Response({
            'message': f'Assigned role to {result["total_successful"]} users',
            'successful': result['successful'],
            'errors': result['errors'],
            'total_successful': result['total_successful'],
            'total_errors': result['total_errors']
        }, status=status.HTTP_201_CREATED if result['successful'] else status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'], url_path='users')
    @swagger_auto_schema(operation_summary='List users assigned to role', tags=['Roles'])
    def users(self, request, pk=None):
        """
        Get all users assigned to this role with their assignment details.
        """
        role = self.get_object()
        assignments = role.assignments.filter(is_active=True).select_related('user', 'location')

        data = []
        for assignment in assignments:
            data.append({
                'assignment_id': assignment.id,
                'user': {
                    'id': assignment.user.id,
                    'username': assignment.user.email,
                    'email': assignment.user.email,
                    'full_name': f"{assignment.user.first_name} {assignment.user.last_name}".strip()
                },
                'location': {
                    'id': assignment.location.id,
                    'name': assignment.location.location
                } if assignment.location else None,
                'assigned_at': assignment.assigned_at,
                'assigned_by': assignment.assigned_by.email if assignment.assigned_by else None,
                'reason': assignment.reason
            })

        return Response(data)

    @action(detail=True, methods=['post'], url_path='deactivate')
    @swagger_auto_schema(operation_summary='Deactivate role', tags=['Roles'])
    def deactivate(self, request, pk=None):
        """
        Deactivate a role (soft delete).
        """
        role = self.get_object()

        if role.is_system_role:
            return Response(
                {'error': 'System roles cannot be deactivated'},
                status=status.HTTP_400_BAD_REQUEST
            )

        role.is_active = False
        role.save()

        return Response({
            'message': f'Role "{role.name}" has been deactivated',
            'role_id': role.id,
            'is_active': role.is_active
        })

    @action(detail=True, methods=['post'], url_path='activate')
    @swagger_auto_schema(operation_summary='Activate role', tags=['Roles'])
    def activate(self, request, pk=None):
        """
        Activate a deactivated role.
        """
        role = self.get_object()
        role.is_active = True
        role.save()

        return Response({
            'message': f'Role "{role.name}" has been activated',
            'role_id': role.id,
            'is_active': role.is_active
        })

    @action(detail=False, methods=['get'], url_path='active')
    @swagger_auto_schema(operation_summary='List active roles', tags=['Roles'])
    def active_roles(self, request):
        """
        Get all active roles.
        """
        roles = Role.objects.filter(is_active=True)
        serializer = self.get_serializer(roles, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='by-priority')
    @swagger_auto_schema(operation_summary='List roles by priority', tags=['Roles'])
    def by_priority(self, request):
        """
        Get roles ordered by approval priority.
        """
        roles = Role.objects.filter(is_active=True).order_by('approval_priority')
        serializer = self.get_serializer(roles, many=True)
        return Response(serializer.data)


# ==================== User Role Assignment Views ====================

class UserRoleAssignmentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing user role assignments.
    """
    queryset = UserRoleAssignment.objects.select_related(
        'user', 'role', 'location', 'assigned_by'
    ).all()
    serializer_class = UserRoleAssignmentSerializer
    permission_classes = [drf_permissions.IsAuthenticated, IsRoleManager]

    def get_permissions(self):
        if self.action in ["my_roles", "my_permissions"]:
            permission_classes = [drf_permissions.IsAuthenticated]
        else:
            permission_classes = self.permission_classes
        return [permission() for permission in permission_classes]

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filter by user
        user_id = self.request.query_params.get('user_id')
        if user_id:
            queryset = queryset.filter(user_id=user_id)

        # Filter by role
        role_id = self.request.query_params.get('role_id')
        if role_id:
            queryset = queryset.filter(role_id=role_id)

        # Filter by location
        location_id = self.request.query_params.get('location_id')
        if location_id:
            queryset = queryset.filter(location_id=location_id)

        # Filter by active status
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')

        return queryset

    def perform_create(self, serializer):
        """Create assignment using service layer."""
        # This is handled in the serializer's create method
        serializer.save()

    @action(detail=True, methods=['post'], url_path='deactivate')
    @swagger_auto_schema(operation_summary='Deactivate role assignment', tags=['Roles'])
    def deactivate(self, request, pk=None):
        """
        Deactivate a role assignment (soft delete).
        """
        assignment = self.get_object()

        if not assignment.is_active:
            return Response(
                {'error': 'Assignment is already inactive'},
                status=status.HTTP_400_BAD_REQUEST
            )

        RoleService.remove_role_from_user(
            user=assignment.user,
            role=assignment.role,
            location=assignment.location
        )

        # Refresh assignment to get updated status
        assignment.refresh_from_db()

        return Response({
            'message': f'Role assignment deactivated',
            'assignment_id': assignment.id,
            'user': assignment.user.email,
            'role': assignment.role.name,
            'is_active': assignment.is_active
        })

    @action(detail=False, methods=['get'], url_path='my-roles')
    @swagger_auto_schema(operation_summary='Get my roles', tags=['Roles'])
    def my_roles(self, request):
        """
        Get roles for the current authenticated user.
        """
        from product.models import Location as ProductLocation

        location = None
        location_id = request.query_params.get('location_id')
        if location_id:
            location = ProductLocation.objects.filter(id=location_id).first()
            if location is None:
                return Response({'error': 'Location not found'}, status=status.HTTP_400_BAD_REQUEST)

        roles = RoleService.get_user_roles(request.user, location=location)
        serializer = RoleSerializer(roles, many=True)

        # Also get active assignments with details
        assignments = UserRoleAssignment.objects.filter(user=request.user, is_active=True).select_related('role', 'location')

        if location is not None:
            normalized = RoleService._normalize_location(location)
            assignments = assignments.filter(Q(location=normalized) | Q(location__isnull=True))

        assignments_data = []
        for assignment in assignments:
            assignments_data.append({
                'assignment_id': assignment.id,
                'role': {
                    'id': assignment.role.id,
                    'name': assignment.role.name,
                    'is_location_based': assignment.role.is_location_based
                },
                'location': {
                    'id': assignment.location.id,
                    'name': assignment.location.location
                } if assignment.location else None,
                'assigned_at': assignment.assigned_at,
                'reason': assignment.reason
            })

        return Response({
            'roles': serializer.data,
            'assignments': assignments_data
        })

    @action(detail=False, methods=['get'], url_path='my-permissions')
    @swagger_auto_schema(operation_summary='Get my permissions', tags=['Roles'])
    def my_permissions(self, request):
        """
        Get all permissions for the current authenticated user.
        """
        from product.models import Location as ProductLocation

        location = None
        location_id = request.query_params.get('location_id')
        if location_id:
            location = ProductLocation.objects.filter(id=location_id).first()
            if location is None:
                return Response({'error': 'Location not found'}, status=status.HTTP_400_BAD_REQUEST)

        permission_objects = RoleService.get_user_permission_objects_via_assignments(request.user, location=location)
        permissions = {f"{p.content_type.app_label}.{p.codename}" for p in permission_objects}

        serializer = PermissionSerializer(permission_objects, many=True)
        return Response({
            'permissions': sorted(list(permissions)),
            'permission_details': serializer.data
        })

    @action(detail=False, methods=['get'], url_path='summary')
    @swagger_auto_schema(operation_summary='Role assignment summary', tags=['Roles'])
    def summary(self, request):
        """
        Get summary of all role assignments (admin only).
        """
        if not request.user.is_staff:
            return Response(
                {'error': 'Admin access required'},
                status=status.HTTP_403_FORBIDDEN
            )

        summary = RoleService.get_role_assignments_summary()
        return Response(summary)


# ==================== User Views ====================

class UserViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing users and their role assignments.
    User creation/modification should go through Django admin or auth endpoints.
    """
    queryset = User.objects.prefetch_related('groups', 'role_assignments')
    permission_classes = [drf_permissions.IsAuthenticated, IsAdminOrReadOnly]

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return UserDetailSerializer
        return UserSerializer

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filter by role
        role_id = self.request.query_params.get('role_id')
        if role_id:
            queryset = queryset.filter(
                role_assignments__role_id=role_id,
                role_assignments__is_active=True
            )

        # Filter by group
        group_id = self.request.query_params.get('group_id')
        if group_id:
            queryset = queryset.filter(groups__id=group_id)

        # Filter by location (users who have roles at a specific location)
        location_id = self.request.query_params.get('location_id')
        if location_id:
            queryset = queryset.filter(
                role_assignments__location_id=location_id,
                role_assignments__is_active=True
            )

        # Search by username, email, or name
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(email__icontains=search) |
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search)
            )

        return queryset.distinct()

    @action(detail=True, methods=['get'], url_path='roles')
    @swagger_auto_schema(operation_summary='List roles for user', tags=['Roles'])
    def roles(self, request, pk=None):
        """
        Get roles assigned to a specific user.
        """
        from product.models import Location as ProductLocation

        location = None
        location_id = request.query_params.get('location_id')
        if location_id:
            location = ProductLocation.objects.filter(id=location_id).first()
            if location is None:
                return Response({'error': 'Location not found'}, status=status.HTTP_400_BAD_REQUEST)

        user = self.get_object()
        roles = RoleService.get_user_roles(user, location=location)
        serializer = RoleSerializer(roles, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='assignments')
    @swagger_auto_schema(operation_summary='List role assignments for user', tags=['Roles'])
    def assignments(self, request, pk=None):
        """
        Get all role assignments for a specific user.
        """
        user = self.get_object()
        assignments = user.role_assignments.filter(is_active=True).select_related('role', 'location')
        serializer = UserRoleAssignmentSerializer(assignments, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='permissions')
    @swagger_auto_schema(operation_summary='List permissions for user', tags=['Roles'])
    def permissions(self, request, pk=None):
        """
        Get all permissions for a specific user.
        """
        from product.models import Location as ProductLocation

        location = None
        location_id = request.query_params.get('location_id')
        if location_id:
            location = ProductLocation.objects.filter(id=location_id).first()
            if location is None:
                return Response({'error': 'Location not found'}, status=status.HTTP_400_BAD_REQUEST)

        user = self.get_object()
        permission_objects = RoleService.get_user_permission_objects_via_assignments(user, location=location)
        permissions = {f"{p.content_type.app_label}.{p.codename}" for p in permission_objects}
        serializer = PermissionSerializer(permission_objects, many=True)
        return Response({
            'user': user.email,
            'permissions': sorted(list(permissions)),
            'permission_details': serializer.data
        })

    @action(detail=True, methods=['post'], url_path='sync-groups')
    @swagger_auto_schema(operation_summary='Sync user groups', tags=['Roles'])
    def sync_groups(self, request, pk=None):
        """
        Synchronize user's group memberships with their role assignments.
        Useful for fixing inconsistencies.
        """
        user = self.get_object()
        result = RoleService.sync_user_groups_with_assignments(user)

        return Response({
            'message': 'User groups synchronized',
            'user': user.email,
            'added_groups': result['added_groups'],
            'removed_groups': result['removed_groups']
        })

    @action(detail=True, methods=['post'], url_path='deactivate-all-roles')
    @swagger_auto_schema(operation_summary='Deactivate all user roles', tags=['Roles'])
    def deactivate_all_roles(self, request, pk=None):
        """
        Deactivate all role assignments for a user.
        """
        user = self.get_object()
        count = RoleService.deactivate_all_user_roles(user, deactivated_by=request.user)

        return Response({
            'message': f'Deactivated {count} role assignments for user {user.email}',
            'count': count
        })


# ==================== Dashboard/Statistics Views ====================

class RoleDashboardView(generics.GenericAPIView):
    """
    View for role management dashboard statistics.
    """
    permission_classes = [drf_permissions.IsAuthenticated, IsRoleManager]
    serializer_class = RoleDashboardResponseSerializer

    @swagger_auto_schema(
        operation_summary='Role dashboard statistics',
        operation_description='Aggregated counts and recent role assignment activity for role management dashboards.',
        responses={200: RoleDashboardResponseSerializer},
        tags=['Roles'],
    )
    def get(self, request):
        """Get dashboard statistics."""
        from django.db.models import Count

        # Total counts
        total_roles = Role.objects.filter(is_active=True).count()
        total_users = User.objects.filter(is_active=True).count()
        total_assignments = UserRoleAssignment.objects.filter(is_active=True).count()
        total_groups = Group.objects.count()

        # Role distribution
        role_distribution = UserRoleAssignment.objects.filter(
            is_active=True
        ).values('role__name').annotate(
            count=Count('id')
        ).order_by('-count')[:10]

        # Location-based vs regular roles
        location_based_roles = Role.objects.filter(is_location_based=True, is_active=True).count()
        regular_roles = total_roles - location_based_roles

        # Recent assignments (last 10)
        recent_assignments = UserRoleAssignment.objects.filter(
            is_active=True
        ).select_related('user', 'role', 'location', 'assigned_by').order_by('-assigned_at')[:10]

        recent_assignments_data = []
        for assignment in recent_assignments:
            recent_assignments_data.append({
                'user': assignment.user.email,
                'role': assignment.role.name,
                'location': assignment.location.location if assignment.location else None,
                'assigned_by': assignment.assigned_by.email if assignment.assigned_by else None,
                'assigned_at': assignment.assigned_at
            })

        return Response({
            'statistics': {
                'total_roles': total_roles,
                'total_users': total_users,
                'total_assignments': total_assignments,
                'total_groups': total_groups,
                'location_based_roles': location_based_roles,
                'regular_roles': regular_roles
            },
            'role_distribution': list(role_distribution),
            'recent_assignments': recent_assignments_data
        })
