from rest_framework.test import APITestCase
from rest_framework import status
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from .models import Location, Stock

User = get_user_model()


class LocationTests(APITestCase):
    def setUp(self):
        # Create user
        self.user = User.objects.create_user(
            email='testuser@example.com',
            password='password123',
            first_name='Test',
            last_name='User'
        )
        # Create group
        self.group, _ = Group.objects.get_or_create(name='Location Manager')
        perms = Permission.objects.filter(
            content_type__app_label='product',
            codename__in=['add_location', 'change_location', 'delete_location'],
        )
        self.group.permissions.set(perms)

        # URL
        self.create_location_url = reverse('create_location')

        self.location_data = {
            'location': 'Warehouse A'
        }

    def test_create_location_unauthenticated(self):
        response = self.client.post(self.create_location_url, self.location_data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_location_unauthorized(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.create_location_url, self.location_data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_location_success(self):
        # Add user to group
        self.user.groups.add(self.group)
        self.client.force_authenticate(user=self.user)

        response = self.client.post(self.create_location_url, self.location_data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Location.objects.count(), 1)
        self.assertEqual(Location.objects.get().location, 'Warehouse A')

    def test_create_nested_location(self):
        self.user.groups.add(self.group)
        self.client.force_authenticate(user=self.user)

        # Create parent
        parent = Location.objects.create(location='Parent')

        # Create child
        data = {
            'location': 'Child',
            'parent': parent.id
        }
        response = self.client.post(self.create_location_url, data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Location.objects.count(), 2)
        child = Location.objects.get(location='Child')
        self.assertEqual(child.parent, parent)

    def test_location_cannot_be_its_own_parent(self):
        self.user.groups.add(self.group)
        self.client.force_authenticate(user=self.user)

        # Create a location
        location = Location.objects.create(location='Self Parent')

        # Try to update it to be its own parent
        url = reverse('location_detail', kwargs={'pk': str(location.id)})
        data = {
            'location': 'Self Parent',
            'parent': str(location.id)
        }
        response = self.client.put(url, data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Location cannot be its own parent', str(response.data))

    def test_update_location_success(self):
        self.user.groups.add(self.group)
        self.client.force_authenticate(user=self.user)

        location = Location.objects.create(location='Old Name')
        url = reverse('location_detail', kwargs={'pk': str(location.id)})
        data = {'location': 'New Name'}

        response = self.client.patch(url, data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Location.objects.get(id=location.id).location, 'New Name')

    def test_update_location_unauthorized(self):
        self.client.force_authenticate(user=self.user)

        location = Location.objects.create(location='Old Name')
        url = reverse('location_detail', kwargs={'pk': str(location.id)})
        data = {'location': 'New Name'}

        response = self.client.patch(url, data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_delete_location_success(self):
        self.user.groups.add(self.group)
        self.client.force_authenticate(user=self.user)

        location = Location.objects.create(location='To Delete')
        url = reverse('location_detail', kwargs={'pk': str(location.id)})

        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(Location.objects.count(), 0)

    def test_delete_location_unauthorized(self):
        self.client.force_authenticate(user=self.user)

        location = Location.objects.create(location='To Delete')
        url = reverse('location_detail', kwargs={'pk': str(location.id)})

        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(Location.objects.count(), 1)


class StockTests(APITestCase):
    def setUp(self):
        # Create user
        self.user = User.objects.create_user(
            email='testuser@example.com',
            password='password123',
            first_name='Test',
            last_name='User'
        )
        self.group, _ = Group.objects.get_or_create(name='Manager Level1')
        perms = Permission.objects.filter(
            content_type__app_label='product',
            codename__in=['add_stock', 'change_stock', 'delete_stock'],
        )
        self.group.permissions.set(perms)
        self.url = reverse('create_stock')
        self.location = Location.objects.create(location='Warehouse A')
        self.stock_data = {
            'part_name': 'Hammer',
            'part_number': 'H123',
            'location': self.location.id,
            'balance': 100,
            'price': 50,
            'brand': 'Generic'
        }

    def test_create_stock_success(self):
        self.user.groups.add(self.group)
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.url, self.stock_data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Stock.objects.count(), 1)
        stock = Stock.objects.get()
        self.assertEqual(stock.part_name, 'Hammer')
        self.assertEqual(stock.balance, 100)
        self.assertEqual(stock.location, self.location)

    def test_create_stock_unauthorized(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.url, self.stock_data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_stock_unauthenticated(self):
        response = self.client.post(self.url, self.stock_data)
        # Assuming default permission is IsAuthenticated or similar, check settings.
        # Based on settings.py read earlier: 'DEFAULT_PERMISSION_CLASSES': ('rest_framework.permissions.IsAuthenticated',)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_stock_invalid_data(self):
        self.user.groups.add(self.group)
        self.client.force_authenticate(user=self.user)
        invalid_data = self.stock_data.copy()
        del invalid_data['part_name']
        response = self.client.post(self.url, invalid_data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_stock_cannot_be_its_own_parent(self):
        self.user.groups.add(self.group)
        self.client.force_authenticate(user=self.user)

        create_response = self.client.post(self.url, self.stock_data)
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        stock = Stock.objects.get()

        url = reverse('stock_detail', kwargs={'pk': str(stock.id)})
        response = self.client.patch(url, {'parent': str(stock.id)})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Part cannot be its own parent', str(response.data))


class LocationImportCSVTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='testuser2@example.com',
            password='password123',
            first_name='Test',
            last_name='User'
        )
        self.superuser = User.objects.create_super_user(
            email='admin@example.com',
            password='password123',
            first_name='Admin',
            last_name='User'
        )
        self.url = reverse('import_locations_csv')

    def test_import_locations_csv_requires_superuser(self):
        csv_content = "location,parent\nFirst Floor,\nB1,First Floor\n"
        upload = SimpleUploadedFile("locations.csv", csv_content.encode("utf-8"), content_type="text/csv")

        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.url, {'file': upload}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_import_locations_csv_success(self):
        csv_content = "location,parent\nFirst Floor,\nB1,First Floor\nB2,First Floor\n"
        upload = SimpleUploadedFile("locations.csv", csv_content.encode("utf-8"), content_type="text/csv")

        self.client.force_authenticate(user=self.superuser)
        response = self.client.post(self.url, {'file': upload}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(Location.objects.filter(location='First Floor', parent__isnull=True).count(), 1)
        parent = Location.objects.get(location='First Floor', parent__isnull=True)
        self.assertEqual(Location.objects.filter(location='B1', parent=parent).count(), 1)
        self.assertEqual(Location.objects.filter(location='B2', parent=parent).count(), 1)

    def test_import_locations_csv_is_idempotent(self):
        csv_content = "location,parent\nFirst Floor,\nB1,First Floor\n"

        self.client.force_authenticate(user=self.superuser)
        upload1 = SimpleUploadedFile("locations.csv", csv_content.encode("utf-8"), content_type="text/csv")
        response1 = self.client.post(self.url, {'file': upload1}, format='multipart')
        self.assertEqual(response1.status_code, status.HTTP_200_OK, response1.data)
        self.assertEqual(Location.objects.count(), 2)

        upload2 = SimpleUploadedFile("locations.csv", csv_content.encode("utf-8"), content_type="text/csv")
        response2 = self.client.post(self.url, {'file': upload2}, format='multipart')
        self.assertEqual(response2.status_code, status.HTTP_200_OK, response2.data)
        self.assertEqual(Location.objects.count(), 2)

    def test_import_locations_csv_dry_run_does_not_write(self):
        csv_content = "location,parent\nFirst Floor,\nB1,First Floor\n"
        upload = SimpleUploadedFile("locations.csv", csv_content.encode("utf-8"), content_type="text/csv")

        self.client.force_authenticate(user=self.superuser)
        response = self.client.post(self.url, {'file': upload, 'dry_run': 'true'}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(Location.objects.count(), 0)
