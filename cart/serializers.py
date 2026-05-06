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

    class Meta:
        model = Cart
        fields = ['id', 'customer_name', 'is_paid', 'is_checked_out', 'checked_out_at', 'items', 'created_at',
                  'updated_at']
