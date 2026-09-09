from rest_framework import status
from rest_framework.test import APITestCase

from members.models import User
from product.models import Location, Stock
from document.models import PurchaseBook, PurchaseApproval


class PurchaseSuperuserFinalApprovalTests(APITestCase):
    def setUp(self):
        self.superuser = User.objects.create_user(
            email="super@example.com",
            password="pass123456",
            first_name="Super",
            last_name="User",
            is_staff=True,
            is_superuser=True,
        )
        self.other_user = User.objects.create_user(
            email="user@example.com",
            password="pass123456",
            first_name="Regular",
            last_name="User",
        )
        self.location = Location.objects.create(location="First Floor")

    def _make_final_step_purchase(self, creator):
        purchase = PurchaseBook.objects.create(
            name="Bolt",
            part_number="B-NEW-100",
            location=self.location,
            price=100,
            quantity=5,
            is_new_product=True,
            brand="CAT",
            is_caterpillar=True,
            is_original=True,
            created_by=creator,
            status="pending",
        )
        PurchaseApproval.objects.create(purchase=purchase, sequence=1, status="confirmed", approved_by=self.other_user)
        PurchaseApproval.objects.create(purchase=purchase, sequence=2, status="confirmed", approved_by=self.other_user)
        PurchaseApproval.objects.create(purchase=purchase, sequence=3, status="pending")
        return purchase

    def _make_step_two_purchase(self, creator):
        purchase = PurchaseBook.objects.create(
            name="Bolt",
            part_number="B-NEW-200",
            location=self.location,
            price=100,
            quantity=5,
            is_new_product=True,
            brand="CAT",
            is_caterpillar=True,
            is_original=True,
            created_by=creator,
            status="pending",
        )
        PurchaseApproval.objects.create(purchase=purchase, sequence=1, status="confirmed", approved_by=self.other_user)
        PurchaseApproval.objects.create(purchase=purchase, sequence=2, status="pending")
        PurchaseApproval.objects.create(purchase=purchase, sequence=3, status="pending")
        return purchase

    def test_superuser_final_approvals_lists_self_created_purchase(self):
        purchase = self._make_final_step_purchase(self.superuser)
        self.client.force_authenticate(user=self.superuser)

        response = self.client.get("/purchases/purchases/admin-final-approvals/")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data[0]["id"], purchase.id)
        self.assertEqual(response.data[0]["current_step"], 3)

    def test_non_superuser_cannot_access_superuser_final_approvals(self):
        self._make_final_step_purchase(self.other_user)
        self.client.force_authenticate(user=self.other_user)

        response = self.client.get("/purchases/purchases/admin-final-approvals/")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_superuser_can_final_approve_own_purchase(self):
        purchase = self._make_final_step_purchase(self.superuser)
        self.client.force_authenticate(user=self.superuser)

        response = self.client.post(
            f"/purchases/purchases/{purchase.id}/approve/",
            {"reason": "Final admin approval"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        purchase.refresh_from_db()
        final_step = purchase.approvals.get(sequence=3)
        created_stock = Stock.objects.get(part_number="B-NEW-100", top_level_location=self.location)

        self.assertEqual(final_step.status, "confirmed")
        self.assertEqual(final_step.approved_by_id, self.superuser.id)
        self.assertEqual(purchase.status, "confirmed")
        self.assertEqual(created_stock.balance, 5)
        self.assertTrue(created_stock.locations.filter(id=self.location.id).exists())

    def test_superuser_can_bulk_approve_only_final_step_purchases(self):
        final_purchase = self._make_final_step_purchase(self.superuser)
        step_two_purchase = self._make_step_two_purchase(self.other_user)
        self.client.force_authenticate(user=self.superuser)

        response = self.client.post(
            "/purchases/purchases/admin-final-approvals/approve-bulk/",
            {"purchase_ids": [final_purchase.id, step_two_purchase.id, 999999, "x"], "reason": "Bulk"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIn(final_purchase.id, response.data["approved"])
        failed_ids = [x["id"] for x in response.data["failed"]]
        self.assertIn(step_two_purchase.id, failed_ids)
        self.assertIn(999999, failed_ids)

        final_purchase.refresh_from_db()
        step_two_purchase.refresh_from_db()
        self.assertEqual(final_purchase.approvals.get(sequence=3).status, "confirmed")
        self.assertEqual(step_two_purchase.approvals.get(sequence=2).status, "pending")

    def test_non_superuser_cannot_bulk_approve_final_step_purchases(self):
        final_purchase = self._make_final_step_purchase(self.other_user)
        self.client.force_authenticate(user=self.other_user)

        response = self.client.post(
            "/purchases/purchases/admin-final-approvals/approve-bulk/",
            {"purchase_ids": [final_purchase.id]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
