from django.db import models
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
from django.conf import settings
import uuid
import secrets
import hashlib
import datetime

class CustomUserManager(BaseUserManager):

    def create_user(self, email, password, **extra_fields):
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save()
        return user

    def create_super_user(self, email, password, **extra_field):
        extra_field.setdefault('is_staff', True)
        extra_field.setdefault('is_superuser', True)

        if extra_field.get('is_staff') is not True:
            raise ValueError('The user has to be a staff')
        if extra_field.get('is_superuser') is not True:
            raise ValueError('The user has to be a super user')
        return self.create_user(email=email, password=password, **extra_field)


class User(AbstractUser):
    username = None
    email = models.EmailField(max_length=80, unique=True)
    first_name = models.CharField(max_length=45)
    last_name = models.CharField(max_length=45)

    objects = CustomUserManager()
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']

    def __str__(self):
        return self.first_name


class SignupInvite(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(max_length=80)
    token_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_signup_invites",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["email", "expires_at", "used_at", "revoked_at"]),
        ]

    @staticmethod
    def _hash_token(raw_token: str) -> str:
        return hashlib.sha256(f"{raw_token}:{settings.SECRET_KEY}".encode("utf-8")).hexdigest()

    @classmethod
    def create_invite(cls, email: str, created_by):
        raw = secrets.token_urlsafe(32)
        token_hash = cls._hash_token(raw)
        now = timezone.now()
        invite = cls.objects.create(
            email=email.strip().lower(),
            token_hash=token_hash,
            created_by=created_by,
            expires_at=now + datetime.timedelta(hours=24),
        )
        return invite, raw

    @classmethod
    def get_valid_invite(cls, raw_token: str):
        token_hash = cls._hash_token(raw_token)
        now = timezone.now()
        return cls.objects.filter(
            token_hash=token_hash,
            used_at__isnull=True,
            revoked_at__isnull=True,
            expires_at__gt=now,
        ).first()


class PasswordResetToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="password_reset_tokens",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_password_reset_tokens",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "expires_at", "used_at", "revoked_at"]),
        ]

    @staticmethod
    def _hash_token(raw_token: str) -> str:
        return hashlib.sha256(f"{raw_token}:{settings.SECRET_KEY}".encode("utf-8")).hexdigest()

    @classmethod
    def create_token(cls, user, created_by, ttl_minutes: int = 30):
        raw = secrets.token_urlsafe(32)
        token_hash = cls._hash_token(raw)
        now = timezone.now()

        cls.objects.filter(user=user, used_at__isnull=True, revoked_at__isnull=True).update(revoked_at=now)

        token = cls.objects.create(
            user=user,
            token_hash=token_hash,
            created_by=created_by,
            expires_at=now + datetime.timedelta(minutes=ttl_minutes),
        )
        return token, raw

    @classmethod
    def get_valid_token(cls, raw_token: str):
        token_hash = cls._hash_token(raw_token)
        now = timezone.now()
        return cls.objects.filter(
            token_hash=token_hash,
            used_at__isnull=True,
            revoked_at__isnull=True,
            expires_at__gt=now,
        ).select_related("user").first()
