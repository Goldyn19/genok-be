from rest_framework import serializers
from .models import Location, Stock
from rest_framework.validators import ValidationError


class LocationSerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()

    class Meta:
        model = Location
        fields = ['id', 'parent', 'location', 'children']

    def validate(self, attrs):
        if self.instance and attrs.get('parent') == self.instance:
            raise ValidationError('Location cannot be its own parent')
        return attrs

    def get_children(self, obj):
        return LocationSerializer(obj.children.all(), many=True).data


class StockSerializer(serializers.ModelSerializer):
    location = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
        write_only=True
    )
    location_detail = LocationSerializer(source='location', read_only=True)
    display_balance = serializers.ReadOnlyField(source="balance")

    class Meta:
        model = Stock
        fields = ['id', 'part_number', 'part_name', 'price', 'location', 'location_detail', 'display_balance', 'balance', 'parent']
        extra_kwargs = {
            'balance': {'write_only': True}
        }

    def validate(self, attrs):
        parent = attrs.get("parent")
        if self.instance is not None and parent == self.instance:
            raise serializers.ValidationError("Part cannot be its own parent")
        return attrs

    def update(self, instance, validated_data):
        if 'balance' in validated_data:
            raise serializers.ValidationError({'balance': 'balance cannot be edited after stock creation'})
        return super().update(instance, validated_data)
