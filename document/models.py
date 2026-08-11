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
    # id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    stock = models.ForeignKey(Stock, on_delete=models.SET_NULL, null=True, blank=True)
    parent_stock = models.ForeignKey(Stock, on_delete=models.SET_NULL, null=True, blank=True, related_name='child_purchases')
    name = models.CharField(max_length=80)
    part_number = models.CharField(max_length=50)
    location = models.ForeignKey(Location, on_delete=models.CASCADE)
    price = models.IntegerField(null=True, blank=True)
    quantity = models.IntegerField()
    is_caterpillar = models.BooleanField(default=True)
    brand = models.CharField(max_length=80, blank=True, null=True)
    is_original = models.BooleanField(default=True)
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

    def _location_ancestor_ids(self):
        ids = []
        loc = self.location
        while loc:
            ids.append(loc.id)
            loc = loc.parent
        return ids

    def _can_user_approve_step_two_for_location(self, user):
        if not user or not getattr(user, 'is_authenticated', False):
            return False

        from roles.models import UserRoleAssignment

        location_ids = self._location_ancestor_ids()
        if not location_ids:
            return False

        return UserRoleAssignment.objects.filter(
            user=user,
            role__is_active=True,
            role__is_location_based=True,
            is_active=True,
            location_id__in=location_ids
        ).exists()

    def can_approve(self, user):
        """
        Check if user can approve the current step using PERMISSIONS.
        """
        if self.status != 'pending':
            return False

        # Get current approval step
        current_approval = self.get_current_approval()
        if not current_approval:
            return False

        # Verify this is the correct step (previous must be complete)
        if not self.is_previous_approval_complete(current_approval):
            return False

        # Superusers can perform final approval even on their own purchases.
        if current_approval.sequence == 3 and getattr(user, 'is_superuser', False):
            return True

        # Creator cannot approve their own purchase
        if user == self.created_by:
            return False

        # Check if user has the PERMISSION for this step
        required_permission = self.get_approval_permission_for_step(current_approval.sequence)
        if not required_permission:
            return False

        if current_approval.sequence == 2:
            return self._can_user_approve_step_two_for_location(user)

        return user.has_perm(required_permission)

    def _temporary_step_two_location_override_allowed(self):
        return getattr(settings, 'TEMP_STEP_TWO_LOCATION_OVERRIDE_ENABLED', True)

    def _validate_step_two_location_override(self, current_approval, location_override):
        if not location_override:
            return
        if not self._temporary_step_two_location_override_allowed():
            raise ValidationError("Temporary location override is currently disabled")
        if current_approval.sequence != 2:
            raise ValidationError("Location can only be changed during second level approval")
        if self.status != 'pending':
            raise ValidationError("Location can only be changed while purchase is pending")
        if location_override == self.location:
            return
        if not self.is_new_product or self.stock_id:
            raise ValidationError("Location override is only allowed for new product purchases")

        normalized_brand = (self.brand or '').strip() or None
        override_root = location_override
        while override_root.parent_id:
            override_root = override_root.parent
        purchase_root = self.location
        while purchase_root.parent_id:
            purchase_root = purchase_root.parent

        if override_root.id == purchase_root.id:
            return

        if not self.is_new_product or self.stock_id:
            raise ValidationError("Location override is only allowed for new product purchases")

    def approve(self, user, reason=None, location_override=None):
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

        self._validate_step_two_location_override(current_approval, location_override)

        approval_reason = reason
        if location_override and location_override != self.location:
            old_location = self.location.location
            new_location = location_override.location
            audit_note = f"TEMP location override: {old_location} -> {new_location}"
            approval_reason = f"{reason.strip()} | {audit_note}" if reason and reason.strip() else audit_note
            self.location = location_override
            self.save(update_fields=['location', 'updated_at'])

        # Record the approval
        current_approval.status = 'confirmed'
        current_approval.reason = approval_reason
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

        if user == self.created_by:
            return False

        if not user.has_perm('document.can_reject_purchase'):
            return False

        current_approval = self.get_current_approval()
        if not current_approval:
            return False

        # Verify previous steps are complete
        if not self.is_previous_approval_complete(current_approval):
            return False

        if current_approval.sequence == 2 and not self._can_user_approve_step_two_for_location(user):
            return False

        return True

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

        normalized_brand = (self.brand or '').strip() or None

        root_location = self.location
        while root_location.parent_id:
            root_location = root_location.parent

        try:
            with transaction.atomic():
                matching_stock = (
                    Stock.objects.select_for_update()
                    .filter(
                        part_number=self.part_number,
                        is_caterpillar=self.is_caterpillar,
                        is_original=self.is_original,
                        brand=normalized_brand,
                        top_level_location=root_location,
                    )
                    .first()
                )

                if matching_stock:
                    self.stock = matching_stock
                    self.is_new_product = False

                if self.is_new_product:
                    self.stock = Stock.objects.create(
                        part_name=self.name,
                        part_number=self.part_number,
                        top_level_location=root_location,
                        balance=self.quantity,
                        price=self.price,
                        is_caterpillar=self.is_caterpillar,
                        brand=normalized_brand,
                        is_original=self.is_original,
                        parent=self.parent_stock,
                    )
                    self.stock.locations.add(self.location)
                    logger.info(
                        f"Purchase #{self.id} - Created new stock item #{self.stock.id} "
                        f"with balance {self.quantity} at top-level {root_location}"
                    )
                    self.save(update_fields=['stock'])
                else:
                    if not self.stock:
                        logger.error(
                            f"Purchase #{self.id} - is_new_product=False but stock is None. "
                            f"Cannot update stock!"
                        )
                        return False

                    if not self.stock.locations.filter(id=self.location.id).exists():
                        self.stock.locations.add(self.location)

                    old_balance = self.stock.balance
                    self.stock.balance += self.quantity
                    self.stock.save(update_fields=['balance'])

                    logger.info(
                        f"Purchase #{self.id} - Updated stock #{self.stock.id} "
                        f"balance from {old_balance} to {self.stock.balance}"
                    )

                    self.is_new_product = False
                    self.save(update_fields=['stock', 'is_new_product'])

                self.status = 'confirmed'
                self.save(update_fields=['status'])
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
    sold_at = models.DateTimeField(default=timezone.now, db_index=True)
    sold_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sales_made'
    )
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sales_entered'
    )

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

    class Meta:
        permissions = [
            ("can_backdate_sale", "Can create backdated sales and assign salesperson"),
        ]

    def recalculate_status(self):
        items = list(self.items.prefetch_related('returns'))
        if not items:
            if self.status != 'pending':
                self.status = 'pending'
                self.save(update_fields=['status'])
            return self.status

        if any(item.status == 'rejected' for item in items):
            next_status = 'rejected'
        elif all(item.status == 'approved' for item in items):
            next_status = 'returned' if all(item.remaining_returnable_quantity == 0 for item in items) else 'approved'
        else:
            next_status = 'pending'

        if self.status != next_status:
            self.status = next_status
            self.save(update_fields=['status'])

        return self.status


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

    @property
    def returned_quantity(self):
        return self.returns.filter(status='approved').aggregate(total=models.Sum('quantity')).get('total') or 0

    @property
    def pending_return_quantity(self):
        return self.returns.filter(status='pending').aggregate(total=models.Sum('quantity')).get('total') or 0

    @property
    def total_requested_return_quantity(self):
        return self.returns.exclude(status='rejected').aggregate(total=models.Sum('quantity')).get('total') or 0

    @property
    def remaining_returnable_quantity(self):
        return max(self.quantity - self.returned_quantity, 0)

    @property
    def remaining_requestable_return_quantity(self):
        return max(self.quantity - self.total_requested_return_quantity, 0)

    @property
    def is_fully_returned(self):
        return self.remaining_returnable_quantity == 0 and self.quantity > 0

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
        self.sales_order.recalculate_status()

    def create_return(self, user, quantity, reason):
        if not user.has_perm('document.can_create_sale_return'):
            raise PermissionDenied("User cannot create sale returns")
        if self.status not in ('pending', 'approved'):
            raise ValidationError("Only pending or approved sales items can have return requests")
        if quantity <= 0:
            raise ValidationError("Return quantity must be greater than 0")
        if not self.product_id:
            raise ValidationError("No stock linked to this sales item")

        with transaction.atomic():
            locked_item = SalesOrderItem.objects.select_for_update().select_related(
                'sales_order'
            ).prefetch_related('returns').get(pk=self.pk)

            if locked_item.status not in ('pending', 'approved'):
                raise ValidationError("Only pending or approved sales items can have return requests")
            if quantity > locked_item.remaining_requestable_return_quantity:
                raise ValidationError(
                    f"Cannot return more than the remaining requestable quantity ({locked_item.remaining_requestable_return_quantity})"
                )

            return_record = SalesReturnItem.objects.create(
                sales_item=locked_item,
                quantity=quantity,
                reason=(reason or "").strip(),
                returned_by=user,
                status='pending',
            )
            SalesReturnApproval.objects.bulk_create([
                SalesReturnApproval(sales_return=return_record, sequence=1),
                SalesReturnApproval(sales_return=return_record, sequence=2),
            ])
            locked_item.sales_order.recalculate_status()
            return return_record


SalesReturnApproval_STATUS_CHOICES = (
    ('pending', 'Pending'),
    ('confirmed', 'Confirmed'),
    ('failed', 'Failed')
)


class SalesReturnItem(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sales_item = models.ForeignKey(SalesOrderItem, on_delete=models.CASCADE, related_name='returns')
    stock = models.ForeignKey(Stock, on_delete=models.SET_NULL, null=True, blank=True, related_name='sale_returns')
    quantity = models.PositiveIntegerField()
    reason = models.TextField()
    returned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sale_returns'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        permissions = [
            ("can_create_sale_return", "Can create sale returns"),
            ("can_approve_sale_return_step_one", "Can approve sale return step one"),
            ("can_approve_sale_return_step_two", "Can approve sale return step two"),
            ("can_reject_sale_return", "Can reject sale return"),
            ("can_view_all_sale_returns", "Can view all sale returns"),
        ]

    def get_approval_permission_for_step(self, step):
        return {
            1: 'document.can_approve_sale_return_step_one',
            2: 'document.can_approve_sale_return_step_two',
        }.get(step)

    def get_current_approval(self):
        return self.approvals.filter(status='pending').order_by('sequence').first()

    def is_previous_complete(self, approval):
        if approval.sequence == 1:
            return True
        previous = self.approvals.filter(sequence=approval.sequence - 1).first()
        return previous and previous.status == 'confirmed'

    def can_approve(self, user):
        if self.status != 'pending':
            return False

        current = self.get_current_approval()
        if not current:
            return False

        if not self.is_previous_complete(current):
            return False

        perm = self.get_approval_permission_for_step(current.sequence)
        return bool(perm) and user.has_perm(perm)

    def can_reject(self, user):
        if self.status != 'pending':
            return False
        current = self.get_current_approval()
        if not current:
            return False
        if not self.is_previous_complete(current):
            return False
        return user.has_perm('document.can_reject_sale_return')

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
        current = self.get_current_approval()
        if current:
            return self.get_approval_permission_for_step(current.sequence)
        return None

    def get_approval_chain_status(self):
        chain = []
        approvals = self.approvals.select_related('approved_by').order_by('sequence')
        for approval in approvals:
            is_blocked = False
            if approval.sequence > 1:
                previous = self.approvals.filter(sequence=approval.sequence - 1).first()
                is_blocked = previous and previous.status != 'confirmed'

            chain.append({
                'step': approval.sequence,
                'required_permission': self.get_approval_permission_for_step(approval.sequence),
                'status': approval.status,
                'is_blocked': is_blocked,
                'can_approve': approval.status == 'pending' and not is_blocked,
                'approved_by': {
                    'id': approval.approved_by.id,
                    'username': approval.approved_by.username,
                    'full_name': approval.approved_by.get_full_name()
                } if approval.approved_by else None,
                'approved_at': approval.approved_at,
                'reason': approval.reason,
            })
        return chain

    def approve(self, user, reason=None):
        if not self.can_approve(user):
            raise PermissionDenied("User cannot approve this sale return")

        with transaction.atomic():
            locked_return = (
                SalesReturnItem.objects.select_for_update()
                .select_related('sales_item', 'sales_item__sales_order')
                .get(pk=self.pk)
            )
            current = locked_return.approvals.select_for_update().filter(status='pending').order_by('sequence').first()
            if not current:
                raise ValidationError("No pending return approval step")
            if not locked_return.is_previous_complete(current):
                raise ValidationError(f"Step {current.sequence - 1} must be completed first")

            current.status = 'confirmed'
            current.reason = reason
            current.approved_by = user
            current.approved_at = timezone.now()
            current.save()

            if not locked_return.approvals.filter(status='pending').exists():
                if not locked_return.sales_item.product_id:
                    raise ValidationError("No stock linked to this sales item")
                stock = Stock.objects.select_for_update().get(pk=locked_return.sales_item.product_id)
                stock.balance += locked_return.quantity
                stock.save(update_fields=['balance'])
                locked_return.status = 'approved'
                locked_return.stock = stock
                locked_return.save(update_fields=['status', 'stock'])
                locked_return.sales_item.sales_order.recalculate_status()

            return current

    def reject(self, user, reason=None):
        if not self.can_reject(user):
            raise PermissionDenied("User cannot reject this sale return")

        with transaction.atomic():
            locked_return = SalesReturnItem.objects.select_for_update().select_related(
                'sales_item', 'sales_item__sales_order'
            ).get(pk=self.pk)
            current = locked_return.approvals.select_for_update().filter(status='pending').order_by('sequence').first()
            if not current:
                raise ValidationError("No pending return approval step")

            current.status = 'failed'
            current.reason = reason or "Rejected"
            current.approved_by = user
            current.approved_at = timezone.now()
            current.save()

            locked_return.status = 'rejected'
            locked_return.save(update_fields=['status'])
            locked_return.sales_item.sales_order.recalculate_status()

    def __str__(self):
        return f"Return {self.id} for sales item {self.sales_item_id} - {self.status}"

    def clean(self):
        if self.quantity <= 0:
            raise ValidationError({'quantity': 'Return quantity must be greater than 0'})
        if not self.reason or not self.reason.strip():
            raise ValidationError({'reason': 'Return reason is required'})

        sales_item = self.sales_item
        if sales_item.status not in ('pending', 'approved'):
            raise ValidationError({'sales_item': 'Only pending or approved sales items can have return requests'})

        existing_qty = sales_item.returns.exclude(pk=self.pk).exclude(status='rejected').aggregate(total=models.Sum('quantity')).get('total') or 0
        if existing_qty + self.quantity > sales_item.quantity:
            raise ValidationError({'quantity': 'Requested return quantity exceeds quantity sold'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class SalesReturnApproval(models.Model):
    sales_return = models.ForeignKey(
        SalesReturnItem,
        on_delete=models.CASCADE,
        related_name='approvals'
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sales_return_approvals'
    )
    sequence = models.IntegerField()
    status = models.CharField(max_length=20, default='pending', choices=SalesReturnApproval_STATUS_CHOICES)
    reason = models.TextField(blank=True, null=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['sequence']
        unique_together = [['sales_return', 'sequence']]
        indexes = [
            models.Index(fields=['sales_return', 'sequence', 'status']),
        ]

    def get_required_permission(self):
        return {
            1: 'document.can_approve_sale_return_step_one',
            2: 'document.can_approve_sale_return_step_two',
        }.get(self.sequence)

    def clean(self):
        if self.sequence > 1:
            previous = self.sales_return.approvals.filter(sequence=self.sequence - 1).first()
            if previous and previous.status != 'confirmed' and self.status == 'confirmed':
                raise ValidationError(
                    f"Cannot approve step {self.sequence} before step {self.sequence - 1} is completed"
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        perm = self.get_required_permission()
        return f"Return step {self.sequence}: {perm} - {self.status}"


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


