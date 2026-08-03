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

    def _location_root(self, location):
        root = location
        while root.parent_id:
            root = root.parent
        return root

    def validate(self, attrs):
        parent = attrs.get("parent")
        if self.instance is not None and parent == self.instance:
            raise serializers.ValidationError("Part cannot be its own parent")

        target_top_level = attrs.get('top_level_location')
        if target_top_level is None and self.instance is not None:
            target_top_level = self.instance.top_level_location

        locations = attrs.get('locations')
        if locations is None and self.instance is not None:
            locations = list(self.instance.locations.all())

        if locations:
            roots = [self._location_root(loc) for loc in locations]
            first_root = roots[0]

            if any(root.id != first_root.id for root in roots[1:]):
                raise serializers.ValidationError({
                    'locations': 'All locations must belong to the same top-level location.'
                })

            if target_top_level is None:
                attrs['top_level_location'] = first_root
                target_top_level = first_root

            if first_root.id != target_top_level.id:
                raise serializers.ValidationError({
                    'locations': (
                        f"Selected locations belong to top-level location '{first_root.location}', "
                        f"but top_level_location is '{target_top_level.location}'."
                    )
                })

        return attrs

    def validate_locations(self, value):
        target_root = None

        raw_top_level = self.initial_data.get('top_level_location')
        if raw_top_level not in (None, '', []):
            target_root = Location.objects.filter(pk=raw_top_level).first()
        elif self.instance is not None:
            target_root = self.instance.top_level_location

        if not target_root:
            return value

        for loc in value:
            root = self._location_root(loc)
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
