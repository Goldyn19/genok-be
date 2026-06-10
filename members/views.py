from django.contrib.auth import authenticate
from rest_framework import status, generics
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from .tokens import create_jwt_pairs_for_users
from .serializers import (
    SignUpSerializer,
    LoginSerializer,
    LoginResponseSerializer,
    InviteCreateSerializer,
    InviteCreateResponseSerializer,
    InviteValidateResponseSerializer,
)
from .models import SignupInvite
from drf_yasg.utils import swagger_auto_schema


class LoginView(APIView):
    permission_classes = []

    @swagger_auto_schema(
        operation_summary='Login user',
        operation_description='Authenticate using email/password and return JWT access/refresh token pair.',
        request_body=LoginSerializer,
        responses={200: LoginResponseSerializer},
        tags=['Auth'],
    )
    def post(self, request: Request):
        email = request.data.get('email')
        password = request.data.get('password')

        user = authenticate(email=email, password=password)
        if user is not None:
            tokens = create_jwt_pairs_for_users(user)
            response = {
                'message': 'Login successful',
                'tokens': tokens
            }
            return Response(data=response, status=status.HTTP_200_OK)
        else:
            return Response(data={'message': 'invalid email or password'}, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        operation_summary='Get current auth context',
        operation_description='Returns the authenticated user and token info (debug helper).',
        tags=['Auth'],
    )
    def get(self, request: Request):
        content = {
            'user': str(request.user),
            'auth': str(request.auth)
        }
        return Response(data=content, status=status.HTTP_200_OK)


class SignUpView(generics.GenericAPIView):
    serializer_class = SignUpSerializer
    permission_classes = []

    @swagger_auto_schema(
        operation_summary='Create user',
        operation_description='Sign up with email/password and profile names using an invite token.',
        request_body=SignUpSerializer,
        responses={201: SignUpSerializer},
        tags=['Auth'],
    )
    def post(self, request: Request):
        data = request.data
        serializer = self.serializer_class(data=data)

        if serializer.is_valid():
            serializer.save()
            response = {
                'message': 'User created successfully',
                'data': serializer.data
            }
            return Response(data=response, status=status.HTTP_201_CREATED)
        return Response(data=serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class InviteCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Create signup invite',
        operation_description='Create a 24-hour invite token for invite-only signup. Superusers only.',
        request_body=InviteCreateSerializer,
        responses={201: InviteCreateResponseSerializer},
        tags=['Auth'],
    )
    def post(self, request: Request):
        if not getattr(request.user, 'is_superuser', False):
            return Response({'detail': 'Only superusers can create invites.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = InviteCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data['email'].strip().lower()
        invite, raw = SignupInvite.create_invite(email=email, created_by=request.user)
        signup_path = f"/signup?token={raw}"
        return Response(
            {'token': raw, 'expires_at': invite.expires_at, 'signup_path': signup_path},
            status=status.HTTP_201_CREATED
        )


class InviteValidateView(APIView):
    permission_classes = []

    @swagger_auto_schema(
        operation_summary='Validate signup invite',
        operation_description='Validate whether an invite token is usable (not expired, not used, not revoked).',
        responses={200: InviteValidateResponseSerializer},
        tags=['Auth'],
    )
    def get(self, request: Request):
        token = (request.query_params.get('token') or '').strip()
        if not token:
            return Response({'detail': 'Missing token'}, status=status.HTTP_400_BAD_REQUEST)

        invite = SignupInvite.get_valid_invite(token)
        if not invite:
            return Response({'valid': False}, status=status.HTTP_200_OK)

        return Response({'valid': True, 'email': invite.email, 'expires_at': invite.expires_at}, status=status.HTTP_200_OK)
# Create your views here.
