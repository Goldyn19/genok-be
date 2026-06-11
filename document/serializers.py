# apps/purchases/serializers.py
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db.models import Q
from django.db import transaction
from django.utils import timezone
from .models import PurchaseBook, PurchaseApproval, SalesOrder, SalesOrderItem, SalesApproval
from product.serializers import LocationSerializer, StockSerializer
from product.models import Location, Stock
from payments.models import CreditID, CreditTransaction
from cart.models import CartItem

User = get_user_model()


# ==================== Base Serializers ====================

class UserBasicSerializer(serializers.ModelSerializer):
    """Basic user serializer for nested responses"""
    username = serializers.SerializerMethodField()
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'full_name']

    def get_username(self, obj):
        return obj.email

    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip() or obj.email


# ==================== PurchaseApproval Serializers ====================

class PurchaseApprovalSerializer(serializers.ModelSerializer):
    """Serializer for PurchaseApproval model"""
    approved_by_details = UserBasicSerializer(source='approved_by', read_only=True)
    required_permission = serializers.SerializerMethodField()
    required_permission_users = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseApproval
        fields = [
            'id', 'sequence', 'status', 'reason',
            'approved_at', 'approved_by', 'approved_by_details',
            'required_permission', 'required_permission_users'
        ]
        read_only_fields = ['id', 'sequence', 'approved_at', 'status']

    def get_required_permission(self, obj):
        """Get the permission required for this approval step"""
        return obj.get_required_permission()

    def get_required_permission_users(self, obj):
        if obj.sequence == 2 and getattr(obj, 'purchase_id', None):
            loc = obj.purchase.location
            location_ids = []
            while loc:
                location_ids.append(loc.id)
                loc = loc.parent

            qs = User.objects.filter(
                role_assignments__role__is_active=True,
                role_assignments__role__is_location_based=True,
                role_assignments__is_active=True,
                role_assignments__location_id__in=location_ids,
                is_active=True
            ).distinct()
            return UserBasicSerializer(qs, many=True).data

        perm_name = obj.get_required_permission()
        if not perm_name or '.' not in perm_name:
            return []

        app_label, codename = perm_name.split('.', 1)
        perm = Permission.objects.filter(content_type__app_label=app_label, codename=codename).first()
        if not perm:
            return []

        qs = User.objects.filter(
            Q(user_permissions=perm) | Q(groups__permissions=perm),
            is_active=True
        ).distinct()
        return UserBasicSerializer(qs, many=True).data


class PurchaseApprovalCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating approval steps (admin only)"""

    class Meta:
        model = PurchaseApproval
        fields = ['sequence', 'role']

    def validate_sequence(self, value):
        """Validate sequence number is positive"""
        if value <= 0:
            raise serializers.ValidationError("Sequence must be greater than 0")
        return value


# ==================== PurchaseBook Serializers ====================

class PurchaseBookListSerializer(serializers.ModelSerializer):
    """Serializer for list view (lightweight)"""
    created_by_name = serializers.ReadOnlyField(source='created_by.email')
    location_details = LocationSerializer(source='location', read_only=True)
    total_amount = serializers.ReadOnlyField()
    approval_progress = serializers.ReadOnlyField()
    current_step = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseBook
        fields = [
            'id', 'name', 'part_number', 'location', 'location_details',
            'quantity', 'price', 'total_amount', 'status',
            'created_by', 'created_by_name', 'created_at',
            'approval_progress', 'current_step'
        ]

    def get_current_step(self, obj):
        return obj.current_step_number


class PurchaseBookDetailSerializer(serializers.ModelSerializer):
    """Serializer for detail view (full details)"""
    created_by_details = UserBasicSerializer(source='created_by', read_only=True)
    location_details = LocationSerializer(source='location', read_only=True)
    stock_details = StockSerializer(source='stock', read_only=True)
    parent_stock_details = StockSerializer(source='parent_stock', read_only=True)
    approvals = PurchaseApprovalSerializer(many=True, read_only=True)
    approval_chain_status = serializers.SerializerMethodField()
    total_amount = serializers.ReadOnlyField()
    approval_progress = serializers.ReadOnlyField()
    current_step_number = serializers.ReadOnlyField()
    current_required_permission = serializers.ReadOnlyField()
    can_current_user_approve = serializers.SerializerMethodField()
    can_current_user_reject = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseBook
        fields = [
            'id', 'name', 'part_number', 'location', 'location_details',
            'price', 'quantity', 'total_amount', 'is_new_product',
            'brand', 'is_caterpillar', 'is_original',
            'stock', 'stock_details', 'parent_stock', 'parent_stock_details', 'status', 'created_by', 'created_by_details',
            'created_at', 'updated_at', 'approvals', 'approval_chain_status',
            'approval_progress', 'current_step_number', 'current_required_permission',
            'can_current_user_approve', 'can_current_user_reject'
        ]

    def get_approval_chain_status(self, obj):
        """Get approval chain status with safe data"""
        request = self.context.get('request')
        user = request.user if request else None

        chain = obj.get_approval_chain_status()

        # Remove sensitive permission names for non-admins
        if user and not user.has_perm('purchases.view_permission_details'):
            for step in chain:
                step.pop('required_permission', None)

        return chain

    def get_can_current_user_approve(self, obj):
        request = self.context.get('request')
        if request and request.user:
            return obj.can_approve(request.user)
        return False

    def get_can_current_user_reject(self, obj):
        request = self.context.get('request')
        if request and request.user:
            return obj.can_reject(request.user)
        return False


class PurchaseBookCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating a new purchase"""
    location = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
        help_text="Location ID where items will be stored"
    )
    parent_stock = serializers.PrimaryKeyRelatedField(
        queryset=Stock.objects.all(),
        required=False,
        allow_null=True
    )
    brand = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    is_caterpillar = serializers.BooleanField(required=False)
    is_original = serializers.BooleanField(required=False)

    class Meta:
        model = PurchaseBook
        fields = [
            'name', 'part_number', 'location', 'price',
            'quantity', 'is_new_product', 'stock', 'parent_stock',
            'brand', 'is_caterpillar', 'is_original'
        ]

    def validate(self, data):
        """Validate purchase data"""
        # Validate quantity
        if data.get('quantity', 0) <= 0:
            raise serializers.ValidationError({'quantity': 'Quantity must be greater than 0'})


        # Validate price is positive
        if data.get('price') is not None and data.get('price') < 0:
            raise serializers.ValidationError({'price': 'Price cannot be negative'})

        # Validate existing stock for non-new products
        if not data.get('is_new_product') and not data.get('stock'):
            raise serializers.ValidationError(
                {'stock': 'Stock is required for existing products'}
            )

        if not data.get('is_new_product'):
            data['parent_stock'] = None
            data['brand'] = None
            data['is_caterpillar'] = data.get('stock').is_caterpillar if data.get('stock') else True
            data['is_original'] = data.get('stock').is_original if data.get('stock') else True

        # Validate stock exists and is active
        if data.get('stock'):
            stock = data['stock']
            if not stock.part_number:
                raise serializers.ValidationError(
                    {'stock': 'Selected stock item is not valid'}
                )

        return data

    def create(self, validated_data):
        """Create purchase and auto-create approval chain"""
        request = self.context.get('request')
        validated_data['created_by'] = request.user

        # Create purchase
        purchase = PurchaseBook.objects.create(**validated_data)

        # Import service here to avoid circular imports
        from .services import PurchaseApprovalService

        # Determine number of approval steps based on total amount
        num_steps = 3

        # Create approval chain
        PurchaseApprovalService.create_approval_chain(purchase, num_steps)

        return purchase


class PurchaseBookUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating a purchase (only before approvals start)"""
    location = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
        required=False,
        help_text="Location ID where items will be stored"
    )
    brand = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    is_caterpillar = serializers.BooleanField(required=False)
    is_original = serializers.BooleanField(required=False)

    class Meta:
        model = PurchaseBook
        fields = ['name', 'part_number', 'location', 'price', 'quantity', 'brand', 'is_caterpillar', 'is_original']
        read_only_fields = ['is_new_product', 'stock']

    def validate(self, data):
        """Only allow updates if no approvals have started"""
        instance = self.instance

        if instance and instance.approvals.filter(status='confirmed').exists():
            raise serializers.ValidationError(
                "Cannot update purchase after approvals have started"
            )

        # Validate price is positive if provided
        if data.get('price') is not None and data.get('price') < 0:
            raise serializers.ValidationError({'price': 'Price cannot be negative'})

        # Validate quantity if provided
        if data.get('quantity') is not None and data.get('quantity') <= 0:
            raise serializers.ValidationError({'quantity': 'Quantity must be greater than 0'})

        if instance and not instance.is_new_product:
            data['brand'] = None
            data['is_caterpillar'] = instance.stock.is_caterpillar if instance.stock else instance.is_caterpillar
            data['is_original'] = instance.stock.is_original if instance.stock else instance.is_original
        elif 'brand' in data:
            data['brand'] = (data.get('brand') or '').strip() or None

        return data

    def update(self, instance, validated_data):
        """Update purchase instance"""
        # Don't allow changing is_new_product or stock
        validated_data.pop('is_new_product', None)
        validated_data.pop('stock', None)

        return super().update(instance, validated_data)


# ==================== Approval Action Serializers ====================

class PurchaseApproveSerializer(serializers.Serializer):
    """Serializer for approve action"""
    reason = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=500)

    def validate_reason(self, value):
        if value and len(value) > 500:
            raise serializers.ValidationError("Reason cannot exceed 500 characters")
        return value


class PurchaseRejectSerializer(serializers.Serializer):
    """Serializer for reject action"""
    reason = serializers.CharField(required=True, allow_blank=False, max_length=500)

    def validate_reason(self, value):
        if not value or len(value.strip()) == 0:
            raise serializers.ValidationError("Rejection reason is required")
        if len(value) > 500:
            raise serializers.ValidationError("Reason cannot exceed 500 characters")
        return value


# ==================== Approval Chain Management Serializers ====================

class ApprovalChainUpdateSerializer(serializers.Serializer):
    """Serializer for updating approval chain (admin only)"""
    num_steps = serializers.IntegerField(min_value=1, max_value=5)

    def validate_num_steps(self, value):
        """Validate number of steps"""
        if value < 1:
            raise serializers.ValidationError("Number of steps must be at least 1")
        if value > 5:
            raise serializers.ValidationError("Number of steps cannot exceed 5")
        return value


# ==================== Query Parameter Serializers ====================

class PurchaseFilterSerializer(serializers.Serializer):
    """Serializer for filtering purchase list"""
    status = serializers.ChoiceField(
        choices=['pending', 'approved', 'confirmed', 'failed'],
        required=False
    )
    location_id = serializers.IntegerField(required=False, allow_null=True)
    created_by_id = serializers.IntegerField(required=False, allow_null=True)
    from_date = serializers.DateField(required=False, allow_null=True)
    to_date = serializers.DateField(required=False, allow_null=True)
    search = serializers.CharField(required=False, allow_null=True, max_length=100)
    min_amount = serializers.DecimalField(required=False, max_digits=15, decimal_places=2, allow_null=True)
    max_amount = serializers.DecimalField(required=False, max_digits=15, decimal_places=2, allow_null=True)

    def validate_from_date(self, value):
        if value and value > timezone.now().date():
            raise serializers.ValidationError("From date cannot be in the future")
        return value

    def validate_to_date(self, value):
        if value and value > timezone.now().date():
            raise serializers.ValidationError("To date cannot be in the future")
        return value

    def validate(self, data):
        """Validate date range"""
        from_date = data.get('from_date')
        to_date = data.get('to_date')

        if from_date and to_date and from_date > to_date:
            raise serializers.ValidationError("From date must be before or equal to To date")

        min_amount = data.get('min_amount')
        max_amount = data.get('max_amount')

        if min_amount and max_amount and min_amount > max_amount:
            raise serializers.ValidationError("Min amount cannot be greater than Max amount")

        return data


# ==================== Dashboard Serializers ====================

class PurchaseDashboardSerializer(serializers.Serializer):
    """Serializer for dashboard statistics"""
    statistics = serializers.DictField()
    approval_by_status = serializers.ListField(child=serializers.DictField())
    recent_purchases = PurchaseBookListSerializer(many=True)
    top_locations = serializers.ListField(child=serializers.DictField())
    top_creators = serializers.ListField(child=serializers.DictField())


# ==================== Export Serializers ====================

class PurchaseExportSerializer(serializers.ModelSerializer):
    """Serializer for exporting purchase data"""
    location_name = serializers.ReadOnlyField(source='location.location')
    created_by_name = serializers.ReadOnlyField(source='created_by.email')
    created_by_email = serializers.ReadOnlyField(source='created_by.email')
    total_amount = serializers.ReadOnlyField()
    approval_completed_date = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseBook
        fields = [
            'id', 'name', 'part_number', 'location_name', 'quantity', 'price',
            'total_amount', 'status', 'created_by_name', 'created_by_email',
            'created_at', 'updated_at', 'approval_completed_date'
        ]

    def get_approval_completed_date(self, obj):
        """Get the date when purchase was fully approved"""
        if obj.status == 'confirmed':
            # Find the last approval
            last_approval = obj.approvals.filter(status='confirmed').order_by('-sequence').first()
            if last_approval and last_approval.approved_at:
                return last_approval.approved_at
        return None


# ==================== SalesApproval Serializers ====================

class SalesApprovalSerializer(serializers.ModelSerializer):
    approved_by_details = UserBasicSerializer(source='approved_by', read_only=True)
    required_permission = serializers.SerializerMethodField()

    class Meta:
        model = SalesApproval
        fields = [
            'id', 'sequence', 'status', 'reason',
            'approved_at', 'approved_by', 'approved_by_details',
            'required_permission'
        ]
        read_only_fields = ['id', 'sequence', 'approved_at', 'status']

    def get_required_permission(self, obj):
        return obj.get_required_permission()
 

class SalesApproveSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=500)


class SalesRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(required=True, allow_blank=False, max_length=500)

    def validate_reason(self, value):
        if not value.strip():
            raise serializers.ValidationError("Rejection reason is required")
        return value

# ==================== SalesOrder Item ====================

class SalesOrderItemSerializer(serializers.ModelSerializer):
    part_name = serializers.CharField(source='product.part_name', read_only=True)
    part_number = serializers.CharField(source='product.part_number', read_only=True)
    approvals = SalesApprovalSerializer(many=True, read_only=True)

    class Meta:
        model = SalesOrderItem
        fields = [
            'id',
            'product',
            'part_name',
            'part_number',
            'quantity',
            'unit_price',
            'total_price',
            'status',
            'approvals',
            'created_at',
        ]
        read_only_fields = ['id', 'total_price', 'created_at']


# ==================== SalesOrder List ====================

class SalesOrderListSerializer(serializers.ModelSerializer):
    total_items = serializers.SerializerMethodField()
    approval_progress = serializers.ReadOnlyField()
    current_step = serializers.SerializerMethodField()

    class Meta:
        model = SalesOrder
        fields = [
            'id',
            'status',
            'payment_method',
            'total_amount',
            'created_at',
            'approval_progress',
            'current_step',
            'total_items',
        ]

    def get_total_items(self, obj):
        return obj.items.count()

    def get_current_step(self, obj):
        return obj.current_step_number


# ==================== SalesOrder Detail ====================

class SalesOrderDetailSerializer(serializers.ModelSerializer):
    items = SalesOrderItemSerializer(many=True, read_only=True)
    approvals = SalesApprovalSerializer(many=True, read_only=True)

    approval_chain_status = serializers.SerializerMethodField()
    approval_progress = serializers.ReadOnlyField()
    current_step_number = serializers.ReadOnlyField()
    current_required_permission = serializers.ReadOnlyField()

    can_current_user_approve = serializers.SerializerMethodField()
    can_current_user_reject = serializers.SerializerMethodField()

    class Meta:
        model = SalesOrder
        fields = [
            'id',
            'cart',
            'credit_customer',
            'payment_method',
            'status',
            'total_amount',
            'notes',
            'items',
            'approvals',
            'approval_chain_status',
            'approval_progress',
            'current_step_number',
            'current_required_permission',
            'can_current_user_approve',
            'can_current_user_reject',
            'created_at',
            'updated_at',
        ]

    def get_approval_chain_status(self, obj):
        request = self.context.get('request')
        user = request.user if request else None

        chain = obj.get_approval_chain_status()

        if user and not user.has_perm('document.view_permission_details'):
            for step in chain:
                step.pop('required_permission', None)

        return chain

    def get_can_current_user_approve(self, obj):
        request = self.context.get('request')
        return obj.can_approve(request.user) if request else False

    def get_can_current_user_reject(self, obj):
        request = self.context.get('request')
        return obj.can_reject(request.user) if request else False


# ==================== SalesOrder Create ====================

class SalesOrderCreateSerializer(serializers.Serializer):
    payment_method = serializers.ChoiceField(choices=SalesOrder.PAYMENT_METHODS)
    credit_customer_id = serializers.UUIDField(required=False)

    def validate(self, data):
        payment_method = data.get('payment_method')
        credit_customer_id = data.get('credit_customer_id')

        if payment_method == 'credit':
            if not credit_customer_id:
                raise serializers.ValidationError({
                    'credit_customer_id': 'Required for credit sales'
                })

            credit_customer = CreditID.objects.filter(credit_id=credit_customer_id).first()
            if not credit_customer:
                raise serializers.ValidationError({
                    'credit_customer_id': 'Credit customer not found'
                })

            data['credit_customer'] = credit_customer

        return data

    def create(self, validated_data):
        request = self.context['request']
        cart = self.context['cart']

        if cart.is_checked_out:
            raise serializers.ValidationError('Cart already checked out')

        items = CartItem.objects.filter(cart=cart).select_related('product')
        if not items.exists():
            raise serializers.ValidationError('Cart has no items')

        payment_method = validated_data['payment_method']
        credit_customer = validated_data.get('credit_customer')

        with transaction.atomic():
            cart.is_checked_out = True
            cart.is_paid = True
            cart.checked_out_at = timezone.now()
            cart.save(update_fields=['is_checked_out', 'is_paid', 'checked_out_at', 'updated_at'])

            sales_order = SalesOrder.objects.create(
                cart=cart,
                credit_customer=credit_customer,
                payment_method=payment_method,
                status='pending'
            )

            total = 0
            sales_items = []

            for item in items:
                unit_price_int = int(item.unit_price)
                line_total_int = int(item.quantity) * unit_price_int
                total += line_total_int

                sales_item = SalesOrderItem.objects.create(
                    sales_order=sales_order,
                    product=item.product,
                    quantity=item.quantity,
                    unit_price=unit_price_int,
                    total_price=line_total_int
                )

                sales_items.append(sales_item)

            # create approval chains
            for sales_item in sales_items:
                SalesApproval.objects.bulk_create([
                    SalesApproval(sales_order=sales_item, sequence=1),
                    SalesApproval(sales_order=sales_item, sequence=2),
                ])

            sales_order.total_amount = total
            sales_order.save(update_fields=['total_amount'])

            if payment_method == 'credit' and credit_customer:
                CreditTransaction.objects.create(
                    customer=credit_customer,
                    sales=sales_order,
                    amount=total,
                )

        return sales_order


# ==================== SalesOrder Update ====================

class SalesOrderUpdateSerializer(serializers.ModelSerializer):

    class Meta:
        model = SalesOrder
        fields = ['notes', 'payment_method']

    def validate(self, data):
        instance = self.instance

        if instance.approvals.filter(status='confirmed').exists():
            raise serializers.ValidationError(
                "Cannot update after approval has started"
            )

        return data


# ==================== Sales Filters ====================

class SalesFilterSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=['pending', 'approved', 'rejected', 'cancelled', 'returned'],
        required=False
    )
    from_date = serializers.DateField(required=False)
    to_date = serializers.DateField(required=False)
    min_amount = serializers.DecimalField(max_digits=15, decimal_places=2, required=False)
    max_amount = serializers.DecimalField(max_digits=15, decimal_places=2, required=False)
