from django.db import models
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser

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
# Create your models here.
