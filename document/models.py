# apps/purchases/models.py
from django.db import models, transaction
from django.utils import timezone
from django.conf import settings
from django.core.exceptions import ValidationError, PermissionDenied
from django.contrib.auth.models import Permission
from product.models import Stock, Location
from cart.models import Cart
import logging
import uuid

logger = logging.getLogger(__name__)

PurchaseApproval_STATUS_CHOICES = (
    ('pending', 'Pending'),
    ('confirmed', 'Confirmed'),
    ('failed', 'Failed')
)

PurchaseBook_STATUS_CHOICES = (
    ('pending', 'Pending'),
    ('approved', 'Approved'),  # All approvals done, waiting for stock update
    ('confirmed', 'Confirmed'),  # Stock updated successfully
    ('failed', 'Failed')
)


class PurchaseBook(models.Model):
    stock = models.ForeignKey(Stock, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=80)
    part_number = models.CharField(max_length=50)
    location = models.ForeignKey(Location, on_delete=models.CASCADE)
    price = models.IntegerField(null=True, blank=True)
    quantity = models.IntegerField()
    is_new_product = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='created_purchases')
    status = models.CharField(max_length=20, default='pending', choices=PurchaseBook_STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        permissions = [
            # Base permissions
            ("can_create_purchase", "Can create purchase"),
            ("can_view_all_purchases", "Can view all purchases"),
            ("can_cancel_purchase", "Can cancel purchase"),

            # Approval step permissions (these define the workflow!)
            ("can_do_first_approval_purchase", "Can do first level purchase approval"),
            ("can_do_second_approval_purchase", "Can do second level purchase approval"),
            ("can_do_final_approval_purchase", "Can do final purchase approval"),

            # Rejection permission
            ("can_reject_purchase", "Can reject purchase"),
        ]

    def clean(self):
        if self.quantity <= 0:
            raise ValidationError({'quantity': 'Quantity must be greater than 0'})
        if self.price is not None and self.price < 0:
            raise ValidationError({'price': 'Price cannot be negative'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def get_approval_permission_for_step(self, step):
        """Get the permission name required for a specific approval step"""
        step_permissions = {
            1: 'document.can_do_first_approval_purchase',
            2: 'document.can_do_second_approval_purchase',
            3: 'document.can_do_final_approval_purchase',
        }
        return step_permissions.get(step)

    def get_current_approval(self):
        """Get the current pending approval step"""
        return self.approvals.filter(status='pending').order_by('sequence').first()

    def get_completed_approvals(self):
        """Get all completed approval steps"""
        return self.approvals.filter(status='confirmed').order_by('sequence')

    def is_previous_approval_complete(self, approval):
        """Check if the previous approval step is complete"""
        if approval.sequence == 1:
            return True
        previous = self.approvals.filter(sequence=approval.sequence - 1).first()
        return previous and previous.status == 'confirmed'

    def can_approve(self, user):
        """
        Check if user can approve the current step using PERMISSIONS.
        """
        if self.status != 'pending':
            return False

        # Creator cannot approve their own purchase
        if user == self.created_by:
            return False

        # Get current approval step
        current_approval = self.get_current_approval()
        if not current_approval:
            return False

        # Verify this is the correct step (previous must be complete)
        if not self.is_previous_approval_complete(current_approval):
            return False

        # Check if user has the PERMISSION for this step
        required_permission = self.get_approval_permission_for_step(current_approval.sequence)
        if not required_permission:
            return False

        return user.has_perm(required_permission)

    def approve(self, user, reason=None):
        """
        Approve the current step using permission-based check.
        """
        if not self.can_approve(user):
            raise PermissionDenied("User cannot approve this purchase")

        current_approval = self.get_current_approval()
        if not current_approval:
            raise ValidationError("No pending approval found")

        # Double-check previous approval is complete
        if not self.is_previous_approval_complete(current_approval):
            raise ValidationError(
                f"Cannot approve step {current_approval.sequence}. "
                f"Step {current_approval.sequence - 1} must be completed first."
            )

        # Record the approval
        current_approval.status = 'confirmed'
        current_approval.reason = reason
        current_approval.approved_at = timezone.now()
        current_approval.approved_by = user
        current_approval.save()

        # Check if all approvals are complete
        if not self.approvals.filter(status='pending').exists():
            # All approvals done - move to approved status
            self.status = 'approved'  # Waiting for stock update
            self.save()

            # Try to update stock
            stock_updated = self.finalize_stock()

            if stock_updated:
                self.status = 'confirmed'
                self.save()
            else:
                # Keep as 'approved' - manual intervention needed
                logger.error(f"Purchase #{self.id} - Stock update failed. Needs manual review.")

        return current_approval

    def can_reject(self, user):
        """Check if user can reject the purchase"""
        if self.status != 'pending':
            return False

        if not user.has_perm('document.can_reject_purchase'):
            return False

        current_approval = self.get_current_approval()
        if not current_approval:
            return False

        # Verify previous steps are complete
        if not self.is_previous_approval_complete(current_approval):
            return False

        # For rejection, user needs the rejection permission OR the step permission
        return user.has_perm('purchases.can_reject_purchase')

    def reject(self, user, reason=None):
        """Reject the purchase at current step"""
        if not self.can_reject(user):
            raise PermissionDenied("User cannot reject this purchase")

        current_approval = self.get_current_approval()
        if current_approval:
            current_approval.status = 'failed'
            current_approval.reason = reason or "Purchase rejected"
            current_approval.approved_at = timezone.now()
            current_approval.approved_by = user
            current_approval.save()

        self.status = 'failed'
        self.save()

    def finalize_stock(self):
        """
        Add items to warehouse stock ONLY after final approval is confirmed.
        Uses status field as the source of truth.

        Returns:
            bool: True if stock was updated, False otherwise
        """
        # === SAFETY CHECK 1: Status must be confirmed ===
        if self.status not in ['approved', 'confirmed']:
            logger.debug(
                f"Purchase #{self.id} - Status is '{self.status}', not 'confirmed'. "
                f"Stock update skipped."
            )
            return False

        # Prevent double update
        if self.status == 'confirmed':
            logger.warning(f"Purchase #{self.id} - Stock already finalized.")
            return True

        # === SAFETY CHECK 2: Prevent duplicate stock updates ===
        # For existing products, check if stock already has this quantity added
        if not self.is_new_product and self.stock:
            # Check if this purchase has already been applied to stock
            # This assumes you have a way to track this - could use a signal or
            # check if the stock balance is suspiciously high
            if hasattr(self, '_stock_updated') or self._is_stock_already_updated():
                logger.warning(
                    f"Purchase #{self.id} - Stock already updated for this purchase. "
                    f"Skipping duplicate update."
                )
                return False

        # === SAFETY CHECK 3: Verify final approval is confirmed ===
        total_approvals = self.approvals.count()
        if total_approvals == 0:
            logger.error(
                f"Purchase #{self.id} has status '{self.status}' but no approval steps defined. "
                f"This is a configuration error!"
            )
            return False

        # Get final approval
        final_approval = self.approvals.filter(sequence=total_approvals).first()
        if not final_approval or final_approval.status != 'confirmed':
            logger.error(
                f"Purchase #{self.id} has status 'confirmed' but final approval "
                f"(step {total_approvals}) status is "
                f"'{final_approval.status if final_approval else 'missing'}'. "
                f"Data inconsistency detected!"
            )
            # Optionally fix the inconsistency
            self.status = 'pending'
            self.save(update_fields=['status'])
            logger.info(f"Purchase #{self.id} - Status reverted to 'pending' due to inconsistency.")
            return False

        # === SAFETY CHECK 4: All approvals must be confirmed ===
        pending_count = self.approvals.filter(status='pending').count()
        if pending_count > 0:
            logger.error(
                f"Purchase #{self.id} has {pending_count} pending approvals "
                f"but status is 'confirmed'. Fixing status..."
            )
            self.status = 'pending'
            self.save(update_fields=['status'])
            return False

        # === SAFETY CHECK 5: For existing stock, prevent double-counting ===
        if not self.is_new_product and self.stock:
            # Check if this purchase might have been processed before
            # This is a safety net - you might want to add a dedicated field
            # or check against purchase history
            pass

        # === All checks passed - proceed with atomic stock update ===
        try:
            with transaction.atomic():
                if self.is_new_product:
                    # Create new stock item
                    self.stock = Stock.objects.create(
                        part_name=self.name,
                        part_number=self.part_number,
                        location=self.location,
                        balance=self.quantity,
                        price=self.price,
                    )
                    logger.info(
                        f"Purchase #{self.id} - Created new stock item #{self.stock.id} "
                        f"with balance {self.quantity}"
                    )
                    self.save(update_fields=['stock'])
                else:
                    # Update existing stock
                    if not self.stock:
                        logger.error(
                            f"Purchase #{self.id} - is_new_product=False but stock is None. "
                            f"Cannot update stock!"
                        )
                        return False

                    old_balance = self.stock.balance
                    self.stock.balance += self.quantity
                    self.stock.save(update_fields=['balance'])

                    logger.info(
                        f"Purchase #{self.id} - Updated stock #{self.stock.id} "
                        f"balance from {old_balance} to {self.stock.balance}"
                    )

                # Mark as confirmed (stock updated successfully)
                self.status = 'confirmed'
                self.save(update_fields=['status'])

                # Mark that stock has been updated (on the instance, not DB)
                self._stock_updated = True

                return True

        except Exception as e:
            logger.exception(
                f"Purchase #{self.id} - Failed to finalize stock: {str(e)}"
            )
            return False

    @property
    def total_amount(self):
        return self.price * self.quantity if self.price else 0

    @property
    def approval_progress(self):
        total = self.approvals.count()
        completed = self.approvals.filter(status='confirmed').count()
        return int((completed / total) * 100) if total > 0 else 0

    @property
    def current_step_number(self):
        current = self.get_current_approval()
        return current.sequence if current else None

    @property
    def current_required_permission(self):
        """Get the permission required for current step"""
        current = self.get_current_approval()
        if current:
            return self.get_approval_permission_for_step(current.sequence)
        return None

    def get_approval_chain_status(self):
        """Get detailed status of approval chain"""
        chain = []
        all_approvals = self.approvals.select_related('approved_by').order_by('sequence')

        for approval in all_approvals:
            is_blocked = False
            if approval.sequence > 1:
                previous = self.approvals.filter(sequence=approval.sequence - 1).first()
                is_blocked = previous and previous.status != 'confirmed'

            required_perm = self.get_approval_permission_for_step(approval.sequence)

            chain.append({
                'step': approval.sequence,
                'required_permission': required_perm,
                'status': approval.status,
                'is_blocked': is_blocked,
                'can_approve': approval.status == 'pending' and not is_blocked,
                'approved_by': {
                    'id': approval.approved_by.id,
                    'username': approval.approved_by.username,
                    'full_name': approval.approved_by.get_full_name()
                } if approval.approved_by else None,
                'approved_at': approval.approved_at,
                'reason': approval.reason
            })
        return chain

    def __str__(self):
        return f"Purchase #{self.id}: {self.name} (Qty: {self.quantity}) - {self.status}"


class PurchaseApproval(models.Model):
    """Approval step for a purchase - each step requires a specific permission"""
    purchase = models.ForeignKey(PurchaseBook, on_delete=models.CASCADE, related_name='approvals')
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='purchase_approvals'
    )
    sequence = models.IntegerField()  # 1, 2, 3, etc. - maps to permission level
    reason = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=20, default='pending', choices=PurchaseApproval_STATUS_CHOICES)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['sequence']
        unique_together = [['purchase', 'sequence']]
        indexes = [
            models.Index(fields=['purchase', 'sequence', 'status']),
        ]

    def get_required_permission(self):
        """Get the permission required for this approval step"""
        step_permissions = {
            1: 'document.can_do_first_approval_purchase',
            2: 'document.can_do_second_approval_purchase',
            3: 'document.can_do_final_approval_purchase',
        }
        return step_permissions.get(self.sequence)

    def clean(self):
        """Validate sequential order"""
        if self.sequence > 1:
            previous = self.purchase.approvals.filter(sequence=self.sequence - 1).first()
            if previous and previous.status != 'confirmed' and self.status == 'confirmed':
                raise ValidationError(
                    f"Cannot approve step {self.sequence} before step {self.sequence - 1} is completed"
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        perm = self.get_required_permission()
        return f"Step {self.sequence}: {perm} - {self.status}"


# SalesBook and approvals

class SalesOrder(models.Model):
    """
    Main sales order record - created when cart is checked out.
    Stock is ONLY updated after all approvals are complete.
    """
    STATUS_CHOICES = (
        ('pending', 'Pending Approval'),
        ('approved', 'Approved - Stock Updated'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
        ('returned', 'Returned')
    )

    # PAYMENT_STATUS = (
    #     ('pending', 'Pending'),
    #     ('paid', 'Paid'),
    #     ('partial', 'Partially Paid'),
    #     ('credit', 'On Credit'),
    # )

    PAYMENT_METHODS = (
        ('cash', 'Cash'),
        ('credit', 'Credit'),
        ('bank_transfer', 'Bank Transfer'),
        ('pos', 'POS'),
    )
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cart = models.OneToOneField(Cart, on_delete=models.SET_NULL, null=True, related_name='sales_order')

    credit_customer = models.ForeignKey(
        'payments.CreditID',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='credit_sales_orders'
    )
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    total_amount = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    notes = models.TextField(blank=True)


class SalesOrderItem(models.Model):
    """
    Individual items within a sales order.
    Tracks what was sold at what price.
    """

    class Meta:
        permissions = [
            ("can_view_all_sales", "Can view all sales"),
            ("can_cancel_sale", "Can cancel sale"),

            ("can_approve_sale_step_one", "Can approve sale step one"),   # floor manager
            ("can_approve_sale_step_two", "Can approve sale step two"),   # CEO

            ("can_reject_sale", "Can reject sale"),
        ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sales_order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name='items')

    product = models.ForeignKey(Stock, on_delete=models.SET_NULL, null=True, related_name='sales_items')

    quantity = models.PositiveIntegerField()
    unit_price = models.IntegerField()
    total_price = models.IntegerField()

    status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pending'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected')
        ],
        default='pending'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def get_approval_permission_for_step(self, step):
        return {
            1: 'document.can_approve_sale_step_one',
            2: 'document.can_approve_sale_step_two',
        }.get(step)

    def get_current_approval(self):
        return self.approvals.filter(status='pending').order_by('sequence').first()

    def is_previous_complete(self, approval):
        if approval.sequence == 1:
            return True
        prev = self.approvals.filter(sequence=approval.sequence - 1).first()
        return prev and prev.status == 'confirmed'

    def can_approve(self, user):
        if self.status != 'pending':
            return False

        current = self.get_current_approval()
        if not current:
            return False

        if not self.is_previous_complete(current):
            return False

        perm = self.get_approval_permission_for_step(current.sequence)
        return user.has_perm(perm)

    def approve(self, user, reason=None):
        if not self.can_approve(user):
            raise PermissionDenied("User cannot approve this sale item")

        current = self.get_current_approval()
        if not current:
            raise ValidationError("No pending approval step")

        if not self.is_previous_complete(current):
            raise ValidationError(
                f"Step {current.sequence - 1} must be completed first"
            )

        current.status = 'confirmed'
        current.reason = reason
        current.approved_by = user
        current.approved_at = timezone.now()
        current.save()

        # check if all approvals done
        if not self.approvals.filter(status='pending').exists():
            self.status = 'approved'
            self.save(update_fields=['status'])

            # update stock immediately per item
            self.update_stock()

            # update parent order
            self.update_parent_order_status()

        return current

    def reject(self, user, reason=None):
        if not user.has_perm('document.can_reject_sale'):
            raise PermissionDenied("User cannot reject this sale")

        current = self.get_current_approval()
        if current:
            current.status = 'failed'
            current.reason = reason or "Rejected"
            current.approved_by = user
            current.approved_at = timezone.now()
            current.save()

        self.status = 'rejected'
        self.save(update_fields=['status'])

        self.update_parent_order_status()

    def update_stock(self):
        if not self.product:
            raise ValidationError("No stock linked to this item")

        if self.product.balance < self.quantity:
            raise ValidationError("Insufficient stock")

        self.product.balance -= self.quantity
        self.product.save(update_fields=['balance'])

    def update_parent_order_status(self):
        items = self.sales_order.items.all()

        if all(item.status == 'approved' for item in items):
            self.sales_order.status = 'approved'
        elif any(item.status == 'rejected' for item in items):
            self.sales_order.status = 'rejected'
        else:
            self.sales_order.status = 'pending'

        self.sales_order.save(update_fields=['status'])


SalesApproval_STATUS_CHOICES = (
    ('pending', 'Pending'),
    ('confirmed', 'Confirmed'),
    ('failed', 'Failed')
)


class SalesApproval(models.Model):
    sales_order = models.ForeignKey(
        SalesOrderItem,
        on_delete=models.CASCADE,
        related_name='approvals'
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sales_approvals'
    )
    sequence = models.IntegerField()  # 1 = manager, 2 = CEO
    status = models.CharField(max_length=20, default='pending', choices=SalesApproval_STATUS_CHOICES)
    reason = models.TextField(blank=True, null=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['sequence']
        unique_together = [['sales_order', 'sequence']]

    def get_required_permission(self):
        step_permissions = {
            1: 'document.can_approve_sale_step_one',
            2: 'document.can_approve_sale_step_two',
        }
        return step_permissions.get(self.sequence)

    def clean(self):
        if self.sequence > 1:
            previous = self.sales_order.approvals.filter(sequence=self.sequence - 1).first()
            if previous and previous.status != 'confirmed' and self.status == 'confirmed':
                raise ValidationError(
                    f"Cannot approve step {self.sequence} before step {self.sequence - 1} is completed"
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        perm = self.get_required_permission()
        return f"Step {self.sequence}: {perm} - {self.status}"


