from rest_framework.test import APITestCase
from rest_framework import status
from django.urls import reverse
from .models import User

class UserTests(APITestCase):
    def setUp(self):
        self.signup_url = reverse('signup')
        self.login_url = reverse('login')
        self.user_data = {
            'email': 'testuser@example.com',
            'password': 'password123',
            'first_name': 'Test',
            'last_name': 'User'
        }
        self.login_data = {
            'email': 'testuser@example.com',
            'password': 'password123'
        }

    def test_signup_success(self):
        response = self.client.post(self.signup_url, self.user_data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['message'], 'User created successfully')
        self.assertEqual(response.data['data']['email'], self.user_data['email'])

    def test_signup_duplicate_email(self):
        self.client.post(self.signup_url, self.user_data)
        response = self.client.post(self.signup_url, self.user_data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_success(self):
        self.client.post(self.signup_url, self.user_data)
        response = self.client.post(self.login_url, self.login_data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['message'], 'Login successful')
        self.assertIn('tokens', response.data)
        self.assertIn('access_token', response.data['tokens'])
        self.assertIn('refresh_token', response.data['tokens'])

    def test_login_failure(self):
        response = self.client.post(self.login_url, {
            'email': 'wrong@example.com',
            'password': 'wrongpassword'
        })
        # Based on current implementation, it returns 200 OK
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['message'], 'invalid email or password')

    def test_login_get_authenticated(self):
        # Create user
        self.client.post(self.signup_url, self.user_data)
        # Login to get token
        login_response = self.client.post(self.login_url, self.login_data)
        access_token = login_response.data['tokens']['access_token']
        
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + access_token)
        response = self.client.get(self.login_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Depending on string representation of user, checking 'testuser@example.com' might be safe or 'Test' (first_name)
        # User.__str__ returns first_name
        self.assertIn('Test', response.data['user']) 

    def test_login_get_unauthenticated(self):
        response = self.client.get(self.login_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('AnonymousUser', response.data['user'])

    def test_jwt_create(self):
        self.client.post(self.signup_url, self.user_data)
        url = reverse('jwt_create')
        response = self.client.post(url, self.login_data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
