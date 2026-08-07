from .models import Cart, CartItem
from rest_framework import serializers


class CartItemSerializer(serializers.ModelSerializer):
    part_number = serializers.CharField(source='product.part_number', read_only=True)
    part_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = CartItem
        fields = ['id', 'product', 'quantity', 'unit_price', 'part_number', 'part_name']


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    sales_order = serializers.SerializerMethodField()

    def _user_summary(self, user):
        if not user:
            return None
        return {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'full_name': getattr(user, 'full_name', '') or '',
        }

    def get_sales_order(self, obj):
        sales_order = getattr(obj, 'sales_order', None)
        if not sales_order:
            return None

        items = list(sales_order.items.all().prefetch_related('approvals'))
        total_approvals = 0
        confirmed_approvals = 0
        current_steps = []

        for item in items:
            approvals = list(item.approvals.all())
            total_approvals += len(approvals)
            confirmed_approvals += sum(1 for approval in approvals if approval.status == 'confirmed')
            pending_steps = [approval.sequence for approval in approvals if approval.status == 'pending']
            if pending_steps:
                current_steps.append(min(pending_steps))

        approval_progress = int((confirmed_approvals / total_approvals) * 100) if total_approvals > 0 else 0
        current_step = min(current_steps) if current_steps else None

        return {
            'id': str(sales_order.id),
            'status': sales_order.status,
            'payment_method': sales_order.payment_method,
            'total_amount': sales_order.total_amount,
            'sold_at': sales_order.sold_at or sales_order.created_at,
            'sold_by': sales_order.sold_by_id,
            'sold_by_details': self._user_summary(sales_order.sold_by),
            'entered_by': sales_order.entered_by_id,
            'entered_by_details': self._user_summary(sales_order.entered_by),
            'created_at': sales_order.created_at,
            'approval_progress': approval_progress,
            'current_step': current_step,
            'total_items': len(items),
        }

    class Meta:
        model = Cart
        fields = ['id', 'customer_name', 'is_paid', 'is_checked_out', 'checked_out_at', 'items', 'sales_order', 'created_at',
                  'updated_at']
