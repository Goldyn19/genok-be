from rest_framework import status
from rest_framework.test import APITestCase

from members.models import User
from product.models import Location
from document.models import PurchaseBook


class ActivityFeedTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="user@example.com",
            password="pass123456",
            first_name="Regular",
            last_name="User",
        )
        self.client.force_authenticate(user=self.user)
        self.location = Location.objects.create(location="Main")

    def test_activity_feed_paginates_union_without_subquery_ordering_error(self):
        PurchaseBook.objects.create(
            name="Bolt",
            part_number="B-100",
            location=self.location,
            price=100,
            quantity=2,
            created_by=self.user,
            status="pending",
            is_new_product=True,
            is_caterpillar=True,
            is_original=True,
            brand="CAT",
        )

        res = self.client.get("/purchases/activity/?page=1&page_size=10")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertIsInstance(res.data, dict)
        self.assertIn("results", res.data)

