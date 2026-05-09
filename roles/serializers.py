from rest_framework import serializers
from django.contrib.auth.models import Group, Permission
from django.contrib.auth import get_user_model
from .models import Role, UserRoleAssignment


User = get_user_model()


class PermissionSerializer(serializers.ModelSerializer):
    """Serializer for Django permissions"""

    class Meta:
        model = Permission
        fields = ['id', 'name', 'codename', 'content_type']


class GroupSerializer(serializers.ModelSerializer):
    """Serializer for Django groups with permission management"""

    permissions = PermissionSerializer(many=True, read_only=True)
    permission_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False
    )
    user_count = serializers.SerializerMethodField()

    class Meta:
        model = Group
        fields = ['id', 'name', 'permissions', 'permission_ids', 'user_count']

    def get_user_count(self, obj):
        """Get number of users in this group"""
        return obj.user_set.count()

    def validate_permission_ids(self, value):
        """
        Validate that all permission IDs exist.
        Return the IDs (not the queryset) for consistency.
        """
        if value:
            permissions = Permission.objects.filter(id__in=value)
            if permissions.count() != len(value):
                raise serializers.ValidationError("Some permissions not found")
        return value  # Return IDs, not queryset

    def create(self, validated_data):
        """Create a new group with permissions"""
        permission_ids = validated_data.pop('permission_ids', [])
        group = Group.objects.create(**validated_data)

        if permission_ids:
            # Fetch permissions and assign
            permissions = Permission.objects.filter(id__in=permission_ids)
            group.permissions.set(permissions)

        return group

    def update(self, instance, validated_data):
        """Update an existing group with permissions"""
        permission_ids = validated_data.pop('permission_ids', None)

        # Update group fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Update permissions if provided
        if permission_ids is not None:
            permissions = Permission.objects.filter(id__in=permission_ids)
            instance.permissions.set(permissions)

        return instance


class RoleSerializer(serializers.ModelSerializer):
    """Serializer for Role model"""

    group_name = serializers.ReadOnlyField(source='required_group.name')
    required_group_id = serializers.ReadOnlyField(source='required_group.id')
    user_count = serializers.SerializerMethodField()
    assignment_count = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = [
            'id', 'name', 'description',
            'required_group', 'required_group_id', 'group_name',
            'is_active', 'is_system_role',
            'approval_priority',
            'is_location_based',
            'location_group_template',
            'user_count', 'assignment_count',
            'created_at'
        ]
        read_only_fields = ['created_at', 'required_group']

    def get_user_count(self, obj):
        """Get number of unique users assigned to this role"""
        return UserRoleAssignment.objects.filter(
            role=obj,
            is_active=True
        ).values('user').distinct().count()

    def get_assignment_count(self, obj):
        """Get total number of active assignments for this role"""
        return obj.assignments.filter(is_active=True).count()

    def validate(self, data):
        """Validate role data"""
        if data.get('is_location_based') and not data.get('location_group_template'):
            raise serializers.ValidationError({
                'location_group_template': 'Location-based roles require a group name template'
            })
        return data

    def create(self, validated_data):
        """
        Create a new role using the service layer.
        Remove any required_group from validated_data to prevent direct injection.
        """
        from .services import RoleService

        # Remove required_group if client included it (enforce service rule)
        validated_data.pop('required_group', None)

        name = validated_data.pop('name')

        role = RoleService.create_role(
            name=name,
            required_group_name=name,
            **validated_data
        )

        # Set created_by if in context
        if self.context.get('request') and self.context['request'].user:
            role.created_by = self.context['request'].user
            role.save()

        return role


class RoleDetailSerializer(RoleSerializer):
    """Detailed role serializer with assignments and permissions"""

    assignments = serializers.SerializerMethodField()
    group_permissions = PermissionSerializer(source='required_group.permissions', many=True, read_only=True)

    class Meta(RoleSerializer.Meta):
        fields = RoleSerializer.Meta.fields + ['assignments', 'group_permissions']

    def get_assignments(self, obj):
        """Get active assignments for this role"""
        assignments = obj.assignments.filter(is_active=True).select_related('user', 'location', 'assigned_by')
        return [
            {
                'id': a.id,
                'user': {
                    'id': a.user.id,
                    'username': a.user.email,
                    'email': a.user.email,
                    'full_name': f"{a.user.first_name} {a.user.last_name}".strip() or a.user.email
                },
                'location': {
                    'id': a.location.id,
                    'name': a.location.location
                } if a.location else None,
                'assigned_by': {
                    'id': a.assigned_by.id,
                    'username': a.assigned_by.email
                } if a.assigned_by else None,
                'assigned_at': a.assigned_at,
                'reason': a.reason
            }
            for a in assignments
        ]


class UserRoleAssignmentSerializer(serializers.ModelSerializer):
    """Serializer for user role assignments"""

    # user_name = serializers.ReadOnlyField(source='user.username')
    user_email = serializers.ReadOnlyField(source='user.email')
    role_name = serializers.ReadOnlyField(source='role.name')
    role_details = RoleSerializer(source='role', read_only=True)
    assigned_by_name = serializers.ReadOnlyField(source='assigned_by.email')
    location_name = serializers.ReadOnlyField(source='location.location')
    normalized_location_name = serializers.SerializerMethodField()

    class Meta:
        model = UserRoleAssignment
        fields = [
            'id', 'user',  'user_email',
            'role', 'role_name', 'role_details',
            'location', 'location_name', 'normalized_location_name',
            'assigned_by', 'assigned_by_name',
            'assigned_at', 'is_active', 'reason'
        ]
        read_only_fields = ['assigned_at', 'assigned_by']

    def get_normalized_location_name(self, obj):
        """Get the normalized location name for display"""
        if obj.location:
            from .services import LocationNormalizer
            normalized = LocationNormalizer.normalize(obj.location)
            return normalized.location if normalized else None
        return None

    def validate(self, data):
        """
        Validate that the assignment doesn't already exist.
        Location is normalized for consistent validation.
        """
        from .services import LocationNormalizer

        role = data['role']
        location = data.get('location')

        # Normalize location for validation
        normalized_location = LocationNormalizer.normalize(location) if location else None

        # Validate location requirement for location-based roles
        if role.is_location_based and not normalized_location:
            raise serializers.ValidationError({
                'location': f"Location is required for role '{role.name}'"
            })

        # Check if assignment already exists with normalized location
        if UserRoleAssignment.objects.filter(
                user=data['user'],
                role=role,
                location=normalized_location,
                is_active=True
        ).exists():
            raise serializers.ValidationError(
                f"User already has active role '{role.name}' assigned"
            )

        return data

    def create(self, validated_data):
        """
        Create a new role assignment using the service layer.
        Location will be normalized in the service.
        """
        from .services import RoleService

        request = self.context.get('request')
        if not request or not request.user:
            raise serializers.ValidationError("Request context missing user information")

        # Let the service handle location normalization
        assignment = RoleService.assign_role_to_user(
            user=validated_data['user'],
            role=validated_data['role'],
            assigned_by=request.user,
            location=validated_data.get('location'),
            reason=validated_data.get('reason', '')
        )

        return assignment

    def update(self, instance, validated_data):
        """
        Update an existing assignment (mainly for deactivation).
        Note: Direct updates to assignments should be limited.
        """
        # Only allow updating is_active and reason
        if 'is_active' in validated_data:
            instance.is_active = validated_data['is_active']
            # If deactivating, use service to properly handle group removal
            if not instance.is_active:
                from .services import RoleService
                RoleService.remove_role_from_user(
                    user=instance.user,
                    role=instance.role,
                    location=instance.location
                )
                return instance

        if 'reason' in validated_data:
            instance.reason = validated_data['reason']

        instance.save()
        return instance


class UserRoleAssignmentCreateSerializer(serializers.Serializer):
    """Serializer for bulk role assignment"""

    user_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        help_text="List of user IDs to assign the role to"
    )
    role_id = serializers.IntegerField(write_only=True, help_text="ID of the role to assign")
    location_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Location ID (required for location-based roles)"
    )
    reason = serializers.CharField(required=False, allow_blank=True, help_text="Reason for assignment")

    def validate_role_id(self, value):
        """Validate that the role exists and is active"""
        try:
            role = Role.objects.get(id=value, is_active=True)
            return role
        except Role.DoesNotExist:
            raise serializers.ValidationError("Role not found or inactive")

    def validate_user_ids(self, value):
        """Validate that all users exist"""
        users = User.objects.filter(id__in=value)
        if users.count() != len(value):
            raise serializers.ValidationError("Some users not found")
        return users

    def validate_location_id(self, value):
        """Validate that the location exists if provided"""
        if value:
            from product.models import Location
            try:
                return Location.objects.get(id=value)
            except Location.DoesNotExist:
                raise serializers.ValidationError("Location not found")
        return None

    def validate(self, data):
        """Cross-field validation"""
        role = data.get('role_id')
        location = data.get('location_id')

        # Check if location is required for location-based role
        if role and role.is_location_based and not location:
            raise serializers.ValidationError({
                'location_id': f"Location is required for role '{role.name}'"
            })

        return data

    def save(self, **kwargs):
        """
        Create assignments for multiple users using the service layer.
        Returns a dict with successful and failed assignments.
        """
        from .services import RoleService

        role = self.validated_data['role_id']
        users = self.validated_data['user_ids']
        location = self.validated_data.get('location_id')
        reason = self.validated_data.get('reason', '')
        assigned_by = self.context['request'].user

        successful = []
        errors = []

        for user in users:
            try:
                assignment = RoleService.assign_role_to_user(
                    user=user,
                    role=role,
                    assigned_by=assigned_by,
                    location=location,
                    reason=reason
                )
                successful.append({
                    'user_id': user.id,
                    'username': user.email,
                    'assignment_id': assignment.id
                })
            except Exception as e:
                errors.append({
                    'user_id': user.id,
                    'username': user.email,
                    'error': str(e)
                })

        return {
            'successful': successful,
            'errors': errors,
            'total_successful': len(successful),
            'total_errors': len(errors)
        }


class UserSerializer(serializers.ModelSerializer):
    """Base user serializer"""

    username = serializers.SerializerMethodField()
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'full_name', 'is_active']
        read_only_fields = ['id']

    def get_username(self, obj):
        return obj.email

    def get_full_name(self, obj):
        """Get user's full name or fallback to username"""
        return f"{obj.first_name} {obj.last_name}".strip() or obj.email


class UserDetailSerializer(UserSerializer):
    """Detailed user serializer with roles and groups"""

    groups = GroupSerializer(many=True, read_only=True)
    roles = serializers.SerializerMethodField()
    active_assignments = serializers.SerializerMethodField()

    class Meta(UserSerializer.Meta):
        fields = UserSerializer.Meta.fields + [
            'groups', 'roles', 'active_assignments', 'last_login', 'date_joined'
        ]

    def get_roles(self, obj):
        """Get active roles for user (unique roles)"""
        from .services import RoleService
        roles = RoleService.get_user_roles(obj)
        return [
            {
                'id': r.id,
                'name': r.name,
                'is_location_based': r.is_location_based,
                'approval_priority': r.approval_priority
            }
            for r in roles
        ]

    def get_active_assignments(self, obj):
        """Get all active role assignments for user with details"""
        assignments = obj.role_assignments.filter(is_active=True).select_related('role', 'location', 'assigned_by')
        return [
            {
                'id': a.id,
                'role': {
                    'id': a.role.id,
                    'name': a.role.name,
                    'is_location_based': a.role.is_location_based,
                    'approval_priority': a.role.approval_priority
                },
                'location': {
                    'id': a.location.id,
                    'name': a.location.location
                } if a.location else None,
                'assigned_at': a.assigned_at,
                'assigned_by': a.assigned_by.email if a.assigned_by else None,
                'reason': a.reason
            }
            for a in assignments
        ]


class GroupUserAddSerializer(serializers.Serializer):
    """Serializer for adding users to group"""

    """
    LOW-LEVEL SERIALIZER - For admin/internal use only.

    Use UserRoleAssignmentSerializer for business role assignments.
    This serializer should only be used for:
    - System maintenance
    - Emergency access
    - Debugging permission issues
    - Direct group management
    """

    user_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        help_text="List of user IDs to add to the group"
    )

    def validate_user_ids(self, value):
        """Validate that all users exist"""
        users = User.objects.filter(id__in=value)
        if users.count() != len(value):
            raise serializers.ValidationError("Some users not found")
        return users


class GroupPermissionAddSerializer(serializers.Serializer):
    """Serializer for adding permissions to group"""

    """
    LOW-LEVEL SERIALIZER - For admin/internal use only.

    Permissions should normally be set via Role.required_group.
    Use this only for:
    - Temporary permission grants
    - Emergency overrides
    - System configuration
    """

    permission_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        help_text="List of permission IDs to add to the group"
    )

    def validate_permission_ids(self, value):
        """Validate that all permissions exist"""
        if value:
            permissions = Permission.objects.filter(id__in=value)
            if permissions.count() != len(value):
                raise serializers.ValidationError("Some permissions not found")
        return value


class RoleDashboardResponseSerializer(serializers.Serializer):
    statistics = serializers.DictField()
    role_distribution = serializers.ListField(child=serializers.DictField())
    recent_assignments = serializers.ListField(child=serializers.DictField())
