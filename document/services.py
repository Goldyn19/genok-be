# apps/purchases/services.py
from django.db import transaction
from django.core.exceptions import ValidationError
from django.contrib.auth.models import Permission
from .models import PurchaseBook, PurchaseApproval, SalesApproval
from django.contrib.auth import get_user_model


import logging

User = get_user_model()
logger = logging.getLogger(__name__)


class PurchaseApprovalService:
    """Service for managing permission-based approval chains"""

    # Map amount ranges to number of approval steps
    FIXED_APPROVAL_STEPS = 3

    @staticmethod
    @transaction.atomic
    def create_approval_chain(purchase, num_steps=None):
        """
        Create sequential approval chain for a purchase.

        Args:
            purchase: PurchaseBook instance
            num_steps: Number of approval steps (1-5)

        Raises:
            ValidationError: If num_steps is invalid
        """
        if num_steps is None:
            num_steps = PurchaseApprovalService.FIXED_APPROVAL_STEPS

        if num_steps < 1 or num_steps > 3:
            raise ValidationError(f"Number of approval steps must be between 1 and 5, got {num_steps}")

        # Verify all required permissions exist
        for step in range(1, num_steps + 1):
            perm_codename = PurchaseApprovalService._get_permission_codename_for_step(step)
            if not Permission.objects.filter(codename=perm_codename).exists():
                raise ValidationError(
                    f"Permission '{perm_codename}' does not exist. "
                    f"Run migrations to create approval permissions."
                )

        # Create approval steps
        for sequence in range(1, num_steps + 1):
            PurchaseApproval.objects.create(
                purchase=purchase,
                sequence=sequence,
                status='pending'
            )

    @staticmethod
    def _get_permission_codename_for_step(step):
        """Get permission codename for a specific approval step"""
        step_map = {
            1: 'can_do_first_approval_purchase',
            2: 'can_do_second_approval_purchase',
            3: 'can_do_final_approval_purchase',
        }
        return step_map.get(step)

    @staticmethod
    def get_next_approvers(purchase):
        """
        Get users who can approve the next step (have the required permission).
        """
        current_approval = purchase.get_current_approval()
        if not current_approval:
            return []

        required_permission = current_approval.get_required_permission()
        if not required_permission:
            return []

        from django.contrib.auth import get_user_model
        User = get_user_model()

        # Get users with the required permission
        # This respects Django's permission system
        users = User.objects.filter(
            groups__permissions__codename=required_permission.split('.')[-1],
            is_active=True
        ).distinct()

        # Also include users with direct permission assignment
        users_with_direct_perm = User.objects.filter(
            user_permissions__codename=required_permission.split('.')[-1],
            is_active=True
        )

        return (users | users_with_direct_perm).distinct()

    @staticmethod
    def get_approval_summary(purchase):
        """
        Get a summary showing which steps are complete and what's next.
        """
        all_approvals = purchase.approvals.order_by('sequence')

        summary = {
            'total_steps': all_approvals.count(),
            'completed_steps': all_approvals.filter(status='confirmed').count(),
            'current_step': None,
            'next_required_permission': None,
            'steps': []
        }

        for approval in all_approvals:
            is_current = (
                    approval.status == 'pending' and
                    (approval.sequence == 1 or
                     purchase.approvals.filter(
                         sequence=approval.sequence - 1,
                         status='confirmed'
                     ).exists())
            )

            step_info = {
                'step': approval.sequence,
                'required_permission': approval.get_required_permission(),
                'status': approval.status,
                'is_complete': approval.status == 'confirmed',
                'is_current': is_current
            }

            if is_current:
                summary['current_step'] = approval.sequence
                summary['next_required_permission'] = approval.get_required_permission()

            summary['steps'].append(step_info)

        return summary

    @staticmethod
    def regenerate_approval_chain(purchase, num_steps):
        """
        Regenerate approval chain with different number of steps.
        WARNING: This resets all approval statuses!
        """
        purchase.approvals.all().delete()
        PurchaseApprovalService.create_approval_chain(purchase, num_steps)

    @staticmethod
    def get_approval_steps_by_amount(total_amount):
        """
        DEPRECATED: Now returns fixed number of steps (5).
        Kept for backward compatibility.
        """
        return PurchaseApprovalService.FIXED_APPROVAL_STEPS


class SalesApprovalService:
    """Service for managing permission-based approval chains for sales items"""

    # Fixed number of approval steps for sales
    FIXED_APPROVAL_STEPS = 2  # Floor Manager (step 1), CEO (step 2)

    @staticmethod
    @transaction.atomic
    def create_approval_chain(sales_item, num_steps=None):
        """
        Create sequential approval chain for a sales order item.

        Args:
            sales_item: SalesOrderItem instance
            num_steps: Number of approval steps (1-3)

        Raises:
            ValidationError: If num_steps is invalid or permissions don't exist
        """
        if num_steps is None:
            num_steps = SalesApprovalService.FIXED_APPROVAL_STEPS

        if num_steps < 1 or num_steps > 3:
            raise ValidationError(
                f"Number of approval steps must be between 1 and 3, got {num_steps}"
            )

        # Verify all required permissions exist
        for step in range(1, num_steps + 1):
            perm_codename = SalesApprovalService._get_permission_codename_for_step(step)
            if not Permission.objects.filter(codename=perm_codename).exists():
                raise ValidationError(
                    f"Permission '{perm_codename}' does not exist. "
                    f"Run migrations to create sales approval permissions."
                )

        # Create approval steps
        for sequence in range(1, num_steps + 1):
            SalesApproval.objects.create(
                sales_order_item=sales_item,
                sequence=sequence,
                status='pending'
            )

        logger.info(
            f"Created {num_steps}-step approval chain for sales item {sales_item.id}"
        )

    @staticmethod
    def _get_permission_codename_for_step(step):
        """
        Get permission codename for a specific approval step.

        Args:
            step: 1, 2, or 3

        Returns:
            str: Permission codename
        """
        step_map = {
            1: 'can_approve_sale_step_one',  # Floor Manager
            2: 'can_approve_sale_step_two',  # CEO
        }
        return step_map.get(step)

    @staticmethod
    def get_next_approvers(sales_item):
        """
        Get users who can approve the next step (have the required permission).

        Args:
            sales_item: SalesOrderItem instance

        Returns:
            QuerySet: Users who can approve the current step
        """
        current_approval = sales_item.get_current_approval()
        if not current_approval:
            return User.objects.none()

        required_permission = sales_item.get_approval_permission_for_step(
            current_approval.sequence
        )
        if not required_permission:
            return User.objects.none()

        # Extract codename from full permission string (e.g., 'sales.can_approve_sale_step_one')
        perm_codename = required_permission.split('.')[-1]

        # Get users with the required permission via groups
        users_from_groups = User.objects.filter(
            groups__permissions__codename=perm_codename,
            is_active=True
        ).distinct()

        # Get users with direct permission assignment
        users_with_direct_perm = User.objects.filter(
            user_permissions__codename=perm_codename,
            is_active=True
        ).distinct()

        # Combine and return unique users
        return (users_from_groups | users_with_direct_perm).distinct()

    @staticmethod
    def get_approval_summary(sales_item):
        """
        Get a summary showing which steps are complete and what's next.

        Args:
            sales_item: SalesOrderItem instance

        Returns:
            dict: Summary of approval status
        """
        all_approvals = sales_item.approvals.order_by('sequence')

        summary = {
            'item_id': str(sales_item.id),
            'item_name': sales_item.product_name,
            'item_status': sales_item.status,
            'total_steps': all_approvals.count(),
            'completed_steps': all_approvals.filter(status='confirmed').count(),
            'current_step': None,
            'next_required_permission': None,
            'is_fully_approved': sales_item.status == 'approved',
            'is_rejected': sales_item.status == 'rejected',
            'steps': []
        }

        for approval in all_approvals:
            # Determine if this is the current pending step
            is_current = (
                    approval.status == 'pending' and
                    (approval.sequence == 1 or
                     sales_item.approvals.filter(
                         sequence=approval.sequence - 1,
                         status='confirmed'
                     ).exists())
            )

            step_info = {
                'step': approval.sequence,
                'required_permission': sales_item.get_approval_permission_for_step(approval.sequence),
                'status': approval.status,
                'is_complete': approval.status == 'confirmed',
                'is_current': is_current,
                'approved_by': {
                    'id': approval.approved_by.id,
                    'username': approval.approved_by.username,
                    'full_name': approval.approved_by.get_full_name()
                } if approval.approved_by else None,
                'approved_at': approval.approved_at,
                'reason': approval.reason
            }

            if is_current:
                summary['current_step'] = approval.sequence
                summary['next_required_permission'] = sales_item.get_approval_permission_for_step(
                    approval.sequence
                )

            summary['steps'].append(step_info)

        return summary

    @staticmethod
    @transaction.atomic
    def regenerate_approval_chain(sales_item, num_steps=None):
        """
        Regenerate approval chain with different number of steps.
        WARNING: This resets all approval statuses!

        Args:
            sales_item: SalesOrderItem instance
            num_steps: New number of steps (defaults to FIXED_APPROVAL_STEPS)
        """
        if num_steps is None:
            num_steps = SalesApprovalService.FIXED_APPROVAL_STEPS

        # Delete existing approvals
        deleted_count = sales_item.approvals.all().delete()[0]

        # Create new approval chain
        SalesApprovalService.create_approval_chain(sales_item, num_steps)

        # Reset item status
        sales_item.status = 'pending'
        sales_item.save(update_fields=['status'])

        logger.warning(
            f"Regenerated approval chain for sales item {sales_item.id}: "
            f"deleted {deleted_count} approvals, created {num_steps} new steps"
        )

        return {
            'item_id': str(sales_item.id),
            'previous_steps_deleted': deleted_count,
            'new_steps_created': num_steps,
            'item_status_reset': sales_item.status
        }

    @staticmethod
    def get_user_pending_approvals(user):
        """
        Get all sales items pending the user's approval.

        Args:
            user: User object

        Returns:
            list: SalesOrderItem instances pending user's approval
        """
        from .models import SalesOrderItem

        pending_items = []

        for item in SalesOrderItem.objects.filter(status='pending').select_related(
                'sales_order', 'product'
        ).prefetch_related('approvals'):
            current_approval = item.get_current_approval()
            if current_approval:
                required_permission = item.get_approval_permission_for_step(
                    current_approval.sequence
                )
                if required_permission and user.has_perm(required_permission):
                    pending_items.append(item)

        return pending_items

    @staticmethod
    def get_user_pending_approvals_count(user):
        """
        Get count of sales items pending the user's approval.

        Args:
            user: User object

        Returns:
            int: Count of pending approvals
        """
        return len(SalesApprovalService.get_user_pending_approvals(user))

    @staticmethod
    def get_approval_statistics():
        """
        Get global approval statistics.

        Returns:
            dict: Statistics about all approvals
        """
        from django.db.models import Count, Q
        from .models import SalesOrderItem

        total_items = SalesOrderItem.objects.count()
        pending_items = SalesOrderItem.objects.filter(status='pending').count()
        approved_items = SalesOrderItem.objects.filter(status='approved').count()
        rejected_items = SalesOrderItem.objects.filter(status='rejected').count()

        # Approval step completion statistics
        approval_stats = SalesApproval.objects.values('sequence').annotate(
            total=Count('id'),
            confirmed=Count('id', filter=Q(status='confirmed')),
            pending=Count('id', filter=Q(status='pending')),
            failed=Count('id', filter=Q(status='failed'))
        ).order_by('sequence')

        return {
            'items': {
                'total': total_items,
                'pending': pending_items,
                'approved': approved_items,
                'rejected': rejected_items,
                'approval_rate': round(
                    (approved_items / total_items * 100) if total_items > 0 else 0, 2
                )
            },
            'approval_steps': list(approval_stats),
            'average_approval_time': None  # Can be calculated with timestamps
        }

    @staticmethod
    def can_user_approve_item(user, sales_item):
        """
        Check if a specific user can approve the current step of an item.

        Args:
            user: User object
            sales_item: SalesOrderItem instance

        Returns:
            bool: True if user can approve, False otherwise
        """
        return sales_item.can_approve(user)

    @staticmethod
    def get_item_approval_progress(sales_item):
        """
        Get detailed approval progress for an item.

        Args:
            sales_item: SalesOrderItem instance

        Returns:
            dict: Detailed progress information
        """
        total_steps = sales_item.approvals.count()
        completed_steps = sales_item.approvals.filter(status='confirmed').count()

        return {
            'item_id': str(sales_item.id),
            'product_name': sales_item.product_name,
            'quantity': sales_item.quantity,
            'unit_price': sales_item.unit_price,
            'total_price': sales_item.total_price,
            'status': sales_item.status,
            'progress_percentage': int((completed_steps / total_steps) * 100) if total_steps > 0 else 0,
            'completed_steps': completed_steps,
            'total_steps': total_steps,
            'current_step': sales_item.get_current_approval().sequence if sales_item.get_current_approval() else None,
            'is_complete': sales_item.status == 'approved',
            'is_rejected': sales_item.status == 'rejected',
            'stock_deducted': sales_item.stock_deducted,
            'stock_deducted_at': sales_item.stock_deducted_at
        }


class SalesOrderBatchApprovalService:
    """
    Service for batch operations on sales approvals.
    Useful for managers who need to approve multiple items at once.
    """

    @staticmethod
    @transaction.atomic
    def batch_approve_items(user, item_ids, reason=None):
        """
        Approve multiple sales items in a single transaction.

        Args:
            user: User performing the approvals
            item_ids: List of SalesOrderItem IDs
            reason: Optional reason for approval

        Returns:
            dict: Results of batch approval
        """
        from .models import SalesOrderItem

        results = {
            'successful': [],
            'failed': [],
            'total': len(item_ids)
        }

        for item_id in item_ids:
            try:
                item = SalesOrderItem.objects.get(id=item_id)

                # Check if user can approve this item
                if not SalesApprovalService.can_user_approve_item(user, item):
                    results['failed'].append({
                        'item_id': item_id,
                        'reason': 'User cannot approve this item'
                    })
                    continue

                # Approve the item
                approval = item.approve(user=user, reason=reason)
                results['successful'].append({
                    'item_id': item_id,
                    'item_name': item.product_name,
                    'step_approved': approval.sequence,
                    'new_status': item.status
                })

            except SalesOrderItem.DoesNotExist:
                results['failed'].append({
                    'item_id': item_id,
                    'reason': 'Item not found'
                })
            except Exception as e:
                results['failed'].append({
                    'item_id': item_id,
                    'reason': str(e)
                })

        return results

    @staticmethod
    def get_items_awaiting_user_approval(user, limit=None):
        """
        Get all items awaiting a specific user's approval.

        Args:
            user: User object
            limit: Optional limit on number of items

        Returns:
            QuerySet: Items pending user's approval
        """
        pending_items = SalesApprovalService.get_user_pending_approvals(user)

        if limit:
            pending_items = pending_items[:limit]

        return pending_items
