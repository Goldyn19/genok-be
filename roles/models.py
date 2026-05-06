from django.db import models
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.conf import settings


class Role(models.Model):
    """
    Dynamic role definition that can be used across multiple apps.
    This is the core model that replaces hardcoded roles.
    """
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    # Django group mapping
    required_group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name='roles',
        help_text="Django group that grants this role"
    )

    # Role configuration
    is_active = models.BooleanField(default=True)
    is_system_role = models.BooleanField(
        default=False,
        help_text="System roles cannot be deleted"
    )

    # Workflow configuration
    approval_priority = models.IntegerField(
        default=0,
        help_text="Lower number = earlier in approval chain"
    )

    # Location-based role configuration
    is_location_based = models.BooleanField(default=False)
    location_group_template = models.CharField(
        max_length=200,
        blank=True,
        help_text="Template like '{location_name} Manager'. "
                  "Available variables: location_name, location_code, location_id"
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_roles'
    )

    class Meta:
        ordering = ['approval_priority', 'name']
        permissions = [
            ("manage_roles", "Can manage roles"),
        ]
        indexes = [
            models.Index(fields=['name', 'is_active']),
            models.Index(fields=['approval_priority']),
            models.Index(fields=['is_location_based']),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.is_location_based and not self.location_group_template:
            raise ValidationError({
                'location_group_template': 'Location-based roles require a group name template'
            })

    def user_has_role(self, user, context_location=None):
        """
        Check if user has this role, optionally with location context.
        Location is normalized for consistency.
        """
        if not self.is_active:
            return False

        # Normalize location if provided
        normalized_location = None
        if context_location:
            from .services import LocationNormalizer
            normalized_location = LocationNormalizer.normalize(context_location)

        # For location-based roles
        if self.is_location_based and normalized_location:
            group_name = self.get_location_group_name(normalized_location)
            return user.groups.filter(name=group_name).exists()

        # For regular roles
        return user.groups.filter(id=self.required_group_id).exists()

    def get_location_group_name(self, location):
        """Generate dynamic group name for a specific location"""
        if not self.is_location_based:
            return None

        return self.location_group_template.format(
            location_name=location.location,
            location_code=getattr(location, 'code', ''),
            location_id=location.id
        )

    def delete(self, *args, **kwargs):
        if self.is_system_role:
            raise ValidationError("System roles cannot be deleted")
        super().delete(*args, **kwargs)


class UserRoleAssignment(models.Model):
    """
    Track role assignments for auditing and temporary assignments
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='role_assignments')
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='assignments')
    location = models.ForeignKey('product.Location', on_delete=models.CASCADE, null=True, blank=True)

    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='assigned_roles')
    assigned_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    reason = models.TextField(blank=True)

    class Meta:
        unique_together = [['user', 'role', 'location']]
        indexes = [
            models.Index(fields=['user', 'is_active']),
        ]

    def __str__(self):
        location_str = f" at {self.location}" if self.location else ""
        return f"{self.user} -> {self.role}{location_str}"


# Create your models here.
