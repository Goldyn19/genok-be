from django.contrib.auth import authenticate
from rest_framework import status, generics
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from .tokens import create_jwt_pairs_for_users
from .serializers import SignUpSerializer, LoginSerializer, LoginResponseSerializer
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
        operation_description='Sign up with email/password and profile names.',
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
# Create your views here.
