from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from product.models import Location, Stock
from .models import Cart, CartItem


User = get_user_model()


class CartEndpointsTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='cartuser@example.com',
            password='password123',
            first_name='Cart',
            last_name='User'
        )
        self.other_user = User.objects.create_user(
            email='othercart@example.com',
            password='password123',
            first_name='Other',
            last_name='User'
        )

        self.location = Location.objects.create(location='Warehouse A')
        self.stock = Stock.objects.create(
            part_name='Hammer',
            part_number='H123',
            location=self.location,
            balance=50,
            price=100
        )

        self.create_cart_url = reverse('create_cart')
        self.user_carts_url = reverse('user_carts')

    def test_create_cart_unauthenticated(self):
        response = self.client.post(self.create_cart_url, {'customer_name': 'ACME'})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_cart_success(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.create_cart_url, {'customer_name': 'ACME'})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Cart.objects.count(), 1)
        cart = Cart.objects.get()
        self.assertEqual(cart.user, self.user)
        self.assertEqual(cart.customer_name, 'ACME')

    def test_list_user_carts_only_returns_user(self):
        Cart.objects.create(user=self.other_user, customer_name='Other Co')
        Cart.objects.create(user=self.user, customer_name='ACME')

        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.user_carts_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['customer_name'], 'ACME')

    def test_cart_detail_success(self):
        cart = Cart.objects.create(user=self.user, customer_name='ACME')

        self.client.force_authenticate(user=self.user)
        url = reverse('cart_detail', kwargs={'id': str(cart.id)})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], str(cart.id))
        self.assertEqual(response.data['customer_name'], 'ACME')

    def test_cart_detail_other_user_returns_404(self):
        other_cart = Cart.objects.create(user=self.other_user, customer_name='Other Co')

        self.client.force_authenticate(user=self.user)
        url = reverse('cart_detail', kwargs={'id': str(other_cart.id)})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cart_detail_unauthenticated(self):
        cart = Cart.objects.create(user=self.user, customer_name='ACME')
        url = reverse('cart_detail', kwargs={'id': str(cart.id)})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_add_item_to_cart_creates_item(self):
        self.client.force_authenticate(user=self.user)
        cart = Cart.objects.create(user=self.user, customer_name='ACME')

        url = reverse('add_cart_item', kwargs={'cart_id': str(cart.id)})
        response = self.client.post(url, {'product': str(self.stock.id), 'quantity': 2})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(CartItem.objects.count(), 1)
        item = CartItem.objects.get()
        self.assertEqual(item.user, self.user)
        self.assertEqual(item.cart, cart)
        self.assertEqual(item.product, self.stock)
        self.assertEqual(item.quantity, 2)

    def test_add_item_to_cart_increments_quantity(self):
        self.client.force_authenticate(user=self.user)
        cart = Cart.objects.create(user=self.user, customer_name='ACME')

        url = reverse('add_cart_item', kwargs={'cart_id': str(cart.id)})
        self.client.post(url, {'product': str(self.stock.id), 'quantity': 2})
        self.client.post(url, {'product': str(self.stock.id), 'quantity': 3})

        self.assertEqual(CartItem.objects.count(), 1)
        item = CartItem.objects.get()
        self.assertEqual(item.quantity, 5)

    def test_add_item_to_other_users_cart_returns_404(self):
        self.client.force_authenticate(user=self.user)
        other_cart = Cart.objects.create(user=self.other_user, customer_name='Other Co')

        url = reverse('add_cart_item', kwargs={'cart_id': str(other_cart.id)})
        response = self.client.post(url, {'product': str(self.stock.id), 'quantity': 1})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_remove_cart_item_success(self):
        self.client.force_authenticate(user=self.user)
        cart = Cart.objects.create(user=self.user, customer_name='ACME')
        item = CartItem.objects.create(user=self.user, cart=cart, product=self.stock, quantity=1)

        url = reverse('remove_cart_item', kwargs={'id': str(item.id)})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(CartItem.objects.count(), 0)

    def test_remove_cart_item_other_user_returns_404(self):
        self.client.force_authenticate(user=self.user)
        cart = Cart.objects.create(user=self.other_user, customer_name='Other Co')
        item = CartItem.objects.create(user=self.other_user, cart=cart, product=self.stock, quantity=1)

        url = reverse('remove_cart_item', kwargs={'id': str(item.id)})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
