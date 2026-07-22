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
    top_level_location = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
        required=False,
        allow_null=True,
    )
    top_level_location_detail = LocationSerializer(source='top_level_location', read_only=True)
    locations = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
        many=True,
        required=False,
    )
    locations_details = LocationSerializer(source='locations', many=True, read_only=True)
    display_balance = serializers.ReadOnlyField(source="balance")

    class Meta:
        model = Stock
        fields = [
            'id',
            'part_number',
            'part_name',
            'price',
            'is_caterpillar',
            'brand',
            'is_original',
            'top_level_location',
            'top_level_location_detail',
            'locations',
            'locations_details',
            'display_balance',
            'balance',
            'parent'
        ]
        extra_kwargs = {
            'balance': {'write_only': True}
        }

    def validate(self, attrs):
        parent = attrs.get("parent")
        if self.instance is not None and parent == self.instance:
            raise serializers.ValidationError("Part cannot be its own parent")
        return attrs

    def validate_locations(self, value):
        top_level = self.instance.top_level_location if self.instance else None
        if not top_level and not self.initial_data.get('top_level_location'):
            return value
        target_root = top_level
        if not target_root:
            raise serializers.ValidationError(
                "top_level_location must be set before adding locations"
            )
        for loc in value:
            root = loc
            while root.parent_id:
                root = root.parent
            if root.id != target_root.id:
                raise serializers.ValidationError(
                    f"Location '{loc.location}' does not belong to top-level location '{target_root.location}'"
                )
        return value

    def update(self, instance, validated_data):
        if 'balance' in validated_data:
            raise serializers.ValidationError({'balance': 'balance cannot be edited after stock creation'})
        locations_data = validated_data.pop('locations', None)
        result = super().update(instance, validated_data)
        if locations_data is not None:
            instance.locations.set(locations_data)
        return result
