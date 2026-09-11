from rest_framework import status
from rest_framework.test import APITestCase

from members.models import User
from product.models import Stock, Location


class StockFamilySearchTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="user@example.com",
            password="pass123456",
            first_name="Regular",
            last_name="User",
        )
        self.client.force_authenticate(user=self.user)

        self.loc = Location.objects.create(location="Main")

        # Chain: A -> B -> C, plus a sibling under A
        self.a = Stock.objects.create(
            part_name="Root",
            part_number="A-100",
            top_level_location=self.loc,
            balance=1,
            price=10,
            is_caterpillar=True,
            is_original=True,
            brand="CAT",
            parent=None,
        )
        self.a.locations.add(self.loc)

        self.b = Stock.objects.create(
            part_name="Child",
            part_number="B-200",
            top_level_location=self.loc,
            balance=1,
            price=10,
            is_caterpillar=True,
            is_original=True,
            brand="CAT",
            parent=self.a,
        )
        self.b.locations.add(self.loc)

        self.c = Stock.objects.create(
            part_name="GrandChild",
            part_number="C-300",
            top_level_location=self.loc,
            balance=1,
            price=10,
            is_caterpillar=True,
            is_original=True,
            brand="CAT",
            parent=self.b,
        )
        self.c.locations.add(self.loc)

        self.sibling = Stock.objects.create(
            part_name="Sibling",
            part_number="A-101",
            top_level_location=self.loc,
            balance=1,
            price=10,
            is_caterpillar=True,
            is_original=True,
            brand="CAT",
            parent=self.a,
        )
        self.sibling.locations.add(self.loc)

    def test_include_family_from_child_returns_root_and_descendants(self):
        res = self.client.get("/product/stock/?q=B-200&include_family=1&page=1&page_size=200")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        results = res.data.get("results") if isinstance(res.data, dict) else res.data
        part_numbers = {r["part_number"] for r in results}
        self.assertEqual(part_numbers, {"A-100", "A-101", "B-200", "C-300"})

    def test_include_family_from_root_returns_descendants(self):
        res = self.client.get("/product/stock/?q=A-100&include_family=1&page=1&page_size=200")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        results = res.data.get("results") if isinstance(res.data, dict) else res.data
        part_numbers = {r["part_number"] for r in results}
        self.assertEqual(part_numbers, {"A-100", "A-101", "B-200", "C-300"})

