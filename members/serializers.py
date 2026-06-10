from rest_framework import serializers
from rest_framework.validators import ValidationError
from rest_framework.authtoken.models import Token
from django.db import transaction
from django.utils import timezone
from .models import User, SignupInvite


class SignUpSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(max_length=80)
    password = serializers.CharField(min_length=8, write_only=True)
    first_name = serializers.CharField(max_length=45)
    last_name = serializers.CharField(max_length=45)
    invite_token = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['email', 'password', 'first_name', 'last_name', 'invite_token']

    def validate(self, attrs):
        email = attrs['email'].strip().lower()
        invite_token = (attrs.get('invite_token') or '').strip()

        if not invite_token:
            raise ValidationError({'invite_token': 'invite token is required'})

        invite = SignupInvite.get_valid_invite(invite_token)
        if not invite:
            raise ValidationError({'invite_token': 'invalid or expired invite token'})

        if invite.email.strip().lower() != email:
            raise ValidationError({'email': 'email does not match invite'})

        email_exists = User.objects.filter(email=email).exists()
        if email_exists:
            raise ValidationError('email already exists')
        attrs['email'] = email
        attrs['_invite_id'] = str(invite.id)
        return super().validate(attrs)

    def create(self, validated_data):
        invite_id = validated_data.pop('_invite_id', None)
        password = validated_data.pop('password')
        validated_data.pop('invite_token', None)

        with transaction.atomic():
            try:
                invite = SignupInvite.objects.select_for_update().get(id=invite_id)
            except SignupInvite.DoesNotExist:
                raise serializers.ValidationError({'invite_token': 'invalid or expired invite token'})

            now = timezone.now()
            if invite.used_at is not None or invite.revoked_at is not None or invite.expires_at <= now:
                raise serializers.ValidationError({'invite_token': 'invalid or expired invite token'})

            user = super().create(validated_data)
            user.set_password(password)
            user.save()
            Token.objects.create(user=user)

            invite.used_at = now
            invite.save(update_fields=['used_at'])

            return user


class InviteCreateSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=80)


class InviteCreateResponseSerializer(serializers.Serializer):
    token = serializers.CharField()
    expires_at = serializers.DateTimeField()
    signup_path = serializers.CharField()


class InviteValidateResponseSerializer(serializers.Serializer):
    valid = serializers.BooleanField()
    email = serializers.EmailField(required=False)
    expires_at = serializers.DateTimeField(required=False)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=80)
    password = serializers.CharField(write_only=True)


class TokenPairSerializer(serializers.Serializer):
    access_token = serializers.CharField()
    refresh_token = serializers.CharField()


class LoginResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    tokens = TokenPairSerializer(required=False)
