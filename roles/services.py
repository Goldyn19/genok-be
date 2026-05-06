from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.core.cache import cache
from .models import Role, UserRoleAssignment
from django.db.models import Q
import logging

logger = logging.getLogger(__name__)

User = get_user_model()


class LocationNormalizer:
    """
    Utility class for consistent location normalization.
    Ensures all location-based operations use the same logic.
    """

    @staticmethod
    def normalize(location):
        """
        Normalize a location by getting its effective parent.
        Rules:
        - If location has a parent, use parent
        - Otherwise use the location itself
        - This ensures consistent group naming across the system
        """
        if not location:
            return None
        return location.parent or location

    @staticmethod
    def get_effective_location_name(location):
        """
        Get the name of the effective location (parent or self).
        Useful for generating consistent group names.
        """
        normalized = LocationNormalizer.normalize(location)
        return normalized.location if normalized else None

    @staticmethod
    def get_effective_location_id(location):
        """
        Get the ID of the effective location.
        """
        normalized = LocationNormalizer.normalize(location)
        return normalized.id if normalized else None


class RoleService:
    """Service layer for role management with consistent location normalization"""

    @staticmethod
    def _cache_delete_pattern(pattern: str):
        delete_pattern = getattr(cache, 'delete_pattern', None)
        if callable(delete_pattern):
            delete_pattern(pattern)
            return
        cache.clear()

    @staticmethod
    def invalidate_user_cache(user_id):
        RoleService._cache_delete_pattern(f'user_roles_{user_id}*')

    @staticmethod
    @transaction.atomic
    def create_role(name, required_group_name, **kwargs):
        """Create a new role with automatic group creation"""
        group, _ = Group.objects.get_or_create(name=required_group_name)

        role = Role.objects.create(
            name=name,
            required_group=group,
            **kwargs
        )

        # Clear role cache
        RoleService._cache_delete_pattern('role_*')

        return role

    @staticmethod
    def _normalize_location(location):
        """Internal helper to normalize location consistently"""
        return LocationNormalizer.normalize(location)

    @staticmethod
    @transaction.atomic
    def assign_role_to_user(user, role, assigned_by, location=None, reason=""):
        """
        Assign a role to a user with auditing.

        Location is automatically normalized for consistency.

        Args:
            user: User to assign role to
            role: Role to assign
            assigned_by: User performing the assignment
            location: Location context (required for location-based roles)
            reason: Reason for assignment
        """
        # Permission check
        if not assigned_by.is_staff:
            raise PermissionError("Only staff users can assign roles")

        # Normalize location for consistent handling
        normalized_location = RoleService._normalize_location(location)

        # Validate location requirement for location-based roles
        if role.is_location_based and not normalized_location:
            raise ValueError(
                f"Location is required for role '{role.name}' because it is location-based. "
                f"Original location: {location}"
            )

        # Check if assignment already exists (including inactive ones) using normalized location
        existing = UserRoleAssignment.objects.filter(
            user=user,
            role=role,
            location=normalized_location  # Use normalized location
        ).first()

        if existing:
            if not existing.is_active:
                # Reactivate inactive assignment
                existing.is_active = True
                existing.reason = reason
                existing.assigned_by = assigned_by
                existing.save()

                # Add user to the appropriate group using normalized location
                RoleService._add_user_to_role_group(user, role, normalized_location)

                # Clear user permissions cache
                RoleService.invalidate_user_cache(user.id)

                return existing
            else:
                raise ValueError(
                    f"User already has active role '{role.name}' assigned "
                    f"for location '{normalized_location}'"
                )

        # Create new assignment with normalized location
        assignment = UserRoleAssignment.objects.create(
            user=user,
            role=role,
            assigned_by=assigned_by,
            location=normalized_location,  # Store normalized location
            reason=reason
        )

        # Add user to the appropriate group using normalized location
        RoleService._add_user_to_role_group(user, role, normalized_location)

        # Clear user permissions cache
        RoleService.invalidate_user_cache(user.id)

        return assignment

    @staticmethod
    def _add_user_to_role_group(user, role, location=None):
        """
        Add user to the appropriate group based on role type.

        Location should already be normalized when calling this method.

        For location-based roles: Add to location-specific group
        For regular roles: Add to role's required group
        """
        if role.is_location_based:
            if not location:
                raise ValueError(
                    f"Location is required for location-based role '{role.name}'. "
                    f"Location should be normalized before calling this method."
                )

            # Generate location-specific group name using normalized location
            group_name = role.get_location_group_name(location)

            # Get or create the location-specific group
            group, created = Group.objects.get_or_create(name=group_name)

            # Copy permissions from template group if new group and template exists
            if created and role.required_group:
                group.permissions.set(role.required_group.permissions.all())
                logger.info(f"Created new group '{group_name}' with permissions from '{role.required_group.name}'")

            user.groups.add(group)
            logger.debug(f"Added user {user.username} to group '{group_name}'")
        else:
            # Regular role - add to role's required group
            user.groups.add(role.required_group)
            logger.debug(f"Added user {user.username} to group '{role.required_group.name}'")

    @staticmethod
    def _remove_user_from_role_group(user, role, location=None):
        """
        Remove user from the appropriate group based on role type.

        Location should already be normalized when calling this method.

        For location-based roles: Remove from location-specific group
        For regular roles: Remove from role's required group
        """
        if role.is_location_based:
            if location:
                group_name = role.get_location_group_name(location)
                try:
                    group = Group.objects.get(name=group_name)
                    user.groups.remove(group)
                    logger.debug(f"Removed user {user.username} from group '{group_name}'")
                except Group.DoesNotExist:
                    logger.warning(f"Group '{group_name}' does not exist for removal")
        else:
            # Regular role - remove from role's required group
            user.groups.remove(role.required_group)
            logger.debug(f"Removed user {user.username} from group '{role.required_group.name}'")

    @staticmethod
    @transaction.atomic
    def remove_role_from_user(user, role, location=None):
        """
        Remove role assignment (soft delete).

        Location is automatically normalized for consistency.

        Args:
            user: User to remove role from
            role: Role to remove
            location: Location context (required for location-based roles)
        """
        # Normalize location
        normalized_location = RoleService._normalize_location(location)

        # For location-based roles, location is required
        if role.is_location_based and not normalized_location:
            raise ValueError(
                f"Location is required to remove location-based role '{role.name}'. "
                f"Original location: {location}"
            )

        # Find the assignment using normalized location
        assignment_filter = {
            'user': user,
            'role': role,
            'is_active': True
        }

        if role.is_location_based:
            assignment_filter['location'] = normalized_location
        elif normalized_location:
            # For non-location-based roles, location is optional in assignment
            assignment_filter['location'] = normalized_location

        assignment = UserRoleAssignment.objects.filter(**assignment_filter).first()

        if not assignment:
            logger.warning(
                f"No active assignment found for user {user.username}, "
                f"role {role.name}, location {normalized_location}"
            )
            return False

        # Soft delete the assignment
        assignment.is_active = False
        assignment.save()

        if role.is_location_based:
            RoleService._remove_user_from_role_group(user, role, normalized_location)
        else:
            other_assignments_exists = UserRoleAssignment.objects.filter(
                user=user,
                role=role,
                is_active=True
            ).exists()

            if not other_assignments_exists:
                RoleService._remove_user_from_role_group(user, role, normalized_location)

        # Clear user permissions cache
        RoleService.invalidate_user_cache(user.id)

        return True

    @staticmethod
    def get_user_roles(user, location=None):
        """
        Get all active roles for a user, optionally filtered by location.

        Location is normalized for consistent filtering.

        Returns unique roles (no duplicates)
        """
        # Normalize location for consistent filtering
        normalized_location = RoleService._normalize_location(location) if location else None

        cache_key = f'user_roles_{user.id}_{normalized_location.id if normalized_location else "all"}'
        cached_roles = cache.get(cache_key)

        if cached_roles is not None:
            return cached_roles

        # Base query for active assignments
        assignments = UserRoleAssignment.objects.filter(
            user=user,
            is_active=True
        ).select_related('role')

        # Filter by normalized location if provided
        if normalized_location:
            assignments = assignments.filter(
                Q(location=normalized_location) | Q(location__isnull=True)
            )

        # Get unique roles (remove duplicates using set)
        roles = list({assignment.role for assignment in assignments})

        cache.set(cache_key, roles, timeout=300)  # Cache for 5 minutes

        return roles

    @staticmethod
    def get_user_permission_objects_via_assignments(user, location=None):
        from django.contrib.auth.models import Permission

        normalized_location = RoleService._normalize_location(location) if location else None

        assignments = UserRoleAssignment.objects.filter(
            user=user,
            is_active=True
        ).select_related('role', 'location', 'role__required_group')

        if normalized_location:
            assignments = assignments.filter(
                Q(location=normalized_location) | Q(location__isnull=True)
            )

        permission_ids = set()

        for assignment in assignments:
            if assignment.role.is_location_based:
                if assignment.location is None:
                    continue
                group_name = assignment.role.get_location_group_name(assignment.location)
                group = Group.objects.filter(name=group_name).prefetch_related('permissions').first()
                if group is None:
                    continue
                permission_ids.update(group.permissions.values_list('id', flat=True))
            else:
                permission_ids.update(assignment.role.required_group.permissions.values_list('id', flat=True))

        return Permission.objects.filter(id__in=list(permission_ids)).select_related('content_type')

    @staticmethod
    def get_user_permissions_via_assignments(user, location=None):
        permissions = RoleService.get_user_permission_objects_via_assignments(user, location=location)
        return {f"{p.content_type.app_label}.{p.codename}" for p in permissions}

    @staticmethod
    def get_permission_trace_via_assignments(user, location=None):
        normalized_location = RoleService._normalize_location(location) if location else None

        assignments = UserRoleAssignment.objects.filter(
            user=user,
            is_active=True
        ).select_related('role', 'location', 'role__required_group').prefetch_related('role__required_group__permissions__content_type')

        if normalized_location:
            assignments = assignments.filter(Q(location=normalized_location) | Q(location__isnull=True))

        trace = {}

        for assignment in assignments:
            if assignment.role.is_location_based:
                if assignment.location is None:
                    continue
                group_name = assignment.role.get_location_group_name(assignment.location)
                group = Group.objects.filter(name=group_name).prefetch_related('permissions__content_type').first()
                if group is None:
                    continue
                permission_iter = group.permissions.all()
            else:
                permission_iter = assignment.role.required_group.permissions.all()

            for perm in permission_iter:
                key = f"{perm.content_type.app_label}.{perm.codename}"
                trace.setdefault(key, []).append({
                    'assignment_id': assignment.id,
                    'role_id': assignment.role.id,
                    'role_name': assignment.role.name,
                })

        return trace

    @staticmethod
    def get_user_active_assignments(user):
        """Get all active role assignments for a user with normalized locations"""
        assignments = UserRoleAssignment.objects.filter(
            user=user,
            is_active=True
        ).select_related('role', 'location', 'assigned_by')

        # Add normalized location info to each assignment
        result = []
        for assignment in assignments:
            assignment_data = {
                'id': assignment.id,
                'user': assignment.user,
                'role': assignment.role,
                'location': assignment.location,
                'normalized_location': RoleService._normalize_location(assignment.location),
                'assigned_by': assignment.assigned_by,
                'assigned_at': assignment.assigned_at,
                'reason': assignment.reason,
                'is_active': assignment.is_active
            }
            result.append(assignment_data)

        return result

    @staticmethod
    @transaction.atomic
    def deactivate_all_user_roles(user, deactivated_by=None):
        """
        Deactivate all roles for a user.

        Args:
            user: User to deactivate roles for
            deactivated_by: User performing the deactivation (optional)
        """
        # Get all active assignments
        assignments = UserRoleAssignment.objects.filter(
            user=user,
            is_active=True
        )

        if not assignments.exists():
            return 0

        # Bulk update to deactivate all assignments at once
        updated_count = assignments.update(
            is_active=False,
            reason=f"Deactivated by {deactivated_by.username if deactivated_by else 'system'}"
        )

        # Get unique groups to remove (using set to avoid duplicates)
        groups_to_remove = set()
        for assignment in assignments.select_related('role'):
            if assignment.role.is_location_based and assignment.location:
                # Location is already normalized in the assignment
                group_name = assignment.role.get_location_group_name(assignment.location)
                try:
                    group = Group.objects.get(name=group_name)
                    groups_to_remove.add(group)
                except Group.DoesNotExist:
                    logger.warning(f"Group '{group_name}' not found for removal")
            else:
                groups_to_remove.add(assignment.role.required_group)

        # Remove user from all groups at once
        for group in groups_to_remove:
            user.groups.remove(group)

        # Clear user permissions cache
        RoleService.invalidate_user_cache(user.id)

        logger.info(f"Deactivated {updated_count} roles for user {user.username}")
        return updated_count

    @staticmethod
    def get_role_assignments_summary():
        """Get summary of all role assignments with normalized location info"""
        from django.db.models import Count

        return UserRoleAssignment.objects.filter(
            is_active=True
        ).values(
            'role__name',
            'role__is_location_based'
        ).annotate(
            user_count=Count('user', distinct=True),
            assignment_count=Count('id')
        ).order_by('-assignment_count')

    @staticmethod
    def get_user_role_assignments_by_location(user):
        """
        Get user's role assignments grouped by normalized location.
        Useful for displaying location-based roles consistently.
        """
        assignments = UserRoleAssignment.objects.filter(
            user=user,
            is_active=True
        ).select_related('role', 'location')

        result = {
            'global_roles': [],
            'location_roles': {}
        }

        for assignment in assignments:
            if assignment.role.is_location_based and assignment.location:
                # Location is already normalized in the assignment
                location_name = assignment.location.location
                if location_name not in result['location_roles']:
                    result['location_roles'][location_name] = {
                        'location_id': assignment.location.id,
                        'location_name': location_name,
                        'roles': []
                    }
                result['location_roles'][location_name]['roles'].append({
                    'assignment_id': assignment.id,
                    'role_id': assignment.role.id,
                    'role_name': assignment.role.name,
                    'assigned_at': assignment.assigned_at,
                    'reason': assignment.reason
                })
            else:
                result['global_roles'].append({
                    'assignment_id': assignment.id,
                    'role_id': assignment.role.id,
                    'role_name': assignment.role.name,
                    'assigned_at': assignment.assigned_at,
                    'reason': assignment.reason
                })

        return result

    @staticmethod
    def validate_user_has_role_for_location(user, role, location):
        """
        Validate if a user has a specific role for a specific location.

        Location is normalized for consistent checking.

        Returns tuple: (is_valid, message)
        """
        if not role.is_active:
            return False, "Role is inactive"

        # Normalize location for consistent checking
        normalized_location = RoleService._normalize_location(location)

        if role.is_location_based:
            if not normalized_location:
                return False, f"Location required for this role. Original location: {location}"

            # Check assignment exists with normalized location
            assignment = UserRoleAssignment.objects.filter(
                user=user,
                role=role,
                location=normalized_location,
                is_active=True
            ).first()

            if not assignment:
                return False, (
                    f"User does not have role '{role.name}' for location '{normalized_location.location}'. "
                    f"Original location: {location}"
                )

            # Verify group membership using normalized location
            group_name = role.get_location_group_name(normalized_location)
            if not user.groups.filter(name=group_name).exists():
                return False, f"User not in required group '{group_name}'"

            return True, "Valid"
        else:
            # Non-location-based role
            assignment = UserRoleAssignment.objects.filter(
                user=user,
                role=role,
                is_active=True
            ).first()

            if not assignment:
                return False, f"User does not have role '{role.name}'"

            if not user.groups.filter(id=role.required_group.id).exists():
                return False, f"User not in required group '{role.required_group.name}'"

            return True, "Valid"

    @staticmethod
    def sync_user_groups_with_assignments(user):
        """
        Synchronize user's group memberships with their active role assignments.
        Uses normalized locations for consistency.
        Useful for fixing inconsistencies.
        """
        # Get all active assignments
        assignments = UserRoleAssignment.objects.filter(
            user=user,
            is_active=True
        ).select_related('role', 'location')

        # Determine which groups user should be in (using normalized locations)
        expected_groups = set()
        for assignment in assignments:
            if assignment.role.is_location_based and assignment.location:
                # Location is already normalized in the assignment
                group_name = assignment.role.get_location_group_name(assignment.location)
                try:
                    group = Group.objects.get(name=group_name)
                    expected_groups.add(group)
                except Group.DoesNotExist:
                    # Create missing group
                    group = Group.objects.create(name=group_name)
                    if assignment.role.required_group:
                        group.permissions.set(assignment.role.required_group.permissions.all())
                    expected_groups.add(group)
                    logger.info(f"Created missing group '{group_name}' during sync")
            else:
                expected_groups.add(assignment.role.required_group)

        # Get current groups
        current_groups = set(user.groups.all())

        # Add missing groups
        groups_to_add = expected_groups - current_groups
        for group in groups_to_add:
            user.groups.add(group)
            logger.info(f"Added user {user.username} to group '{group.name}' during sync")

        from product.models import Location as ProductLocation

        role_related_group_names = set(
            Role.objects.filter(is_active=True, is_location_based=False)
            .select_related('required_group')
            .values_list('required_group__name', flat=True)
        )

        for role in Role.objects.filter(is_active=True, is_location_based=True):
            for loc in ProductLocation.objects.all():
                normalized = RoleService._normalize_location(loc)
                if normalized is None:
                    continue
                role_related_group_names.add(role.get_location_group_name(normalized))

        groups_to_remove = current_groups - expected_groups
        removed_groups = []
        for group in groups_to_remove:
            if group.name in role_related_group_names:
                user.groups.remove(group)
                removed_groups.append(group.name)
                logger.info(f"Removed user {user.username} from group '{group.name}' during sync")

        # Clear cache
        RoleService.invalidate_user_cache(user.id)

        return {
            'added_groups': [g.name for g in groups_to_add],
            'removed_groups': removed_groups
        }

    @staticmethod
    def get_users_by_role_and_location(role, location=None):
        """
        Get all users who have a specific role, optionally filtered by location.
        Location is normalized for consistent filtering.

        Returns list of users with their assignment details.
        """
        normalized_location = RoleService._normalize_location(location) if location else None

        assignment_filter = {
            'role': role,
            'is_active': True
        }

        if normalized_location:
            assignment_filter['location'] = normalized_location

        assignments = UserRoleAssignment.objects.filter(
            **assignment_filter
        ).select_related('user', 'location')

        result = []
        for assignment in assignments:
            result.append({
                'user': assignment.user,
                'assignment_id': assignment.id,
                'location': assignment.location,
                'assigned_at': assignment.assigned_at,
                'assigned_by': assignment.assigned_by,
                'reason': assignment.reason
            })

        return result
