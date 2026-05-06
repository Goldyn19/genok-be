from rest_framework import status, generics, mixins, permissions as drf_permissions
from .models import Location, Stock
from .serializers import LocationSerializer, StockSerializer
from rest_framework.response import Response
from rest_framework.request import Request
from drf_yasg.utils import swagger_auto_schema
from django.db.models import Q


class LocationCreateView(generics.GenericAPIView, mixins.CreateModelMixin):
    serializer_class = LocationSerializer
    queryset = Location.objects.all()
    permission_classes = [drf_permissions.IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Create location',
        operation_description='Create a location (optionally nested under a parent).',
        request_body=LocationSerializer,
        responses={201: LocationSerializer},
        tags=['Product'],
    )
    def post(self, request: Request, *args, **kwargs):
        user = getattr(self.request, 'user', None)
        if not user.has_perm('product.add_location'):
            return Response(
                {
                    'error': 'Permission denied',
                    'detail': 'You do not have permission to create locations.',
                },
                status=status.HTTP_403_FORBIDDEN
            )
        return self.create(request, *args, **kwargs)


class LocationRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = LocationSerializer
    queryset = Location.objects.all()
    permission_classes = [drf_permissions.IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get location',
        operation_description='Retrieve a location by id.',
        responses={200: LocationSerializer},
        tags=['Product'],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_summary='Update location',
        operation_description='Update a location by id.',
        request_body=LocationSerializer,
        responses={200: LocationSerializer},
        tags=['Product'],
    )
    def put(self, request, *args, **kwargs):
        user = getattr(self.request, 'user', None)
        if not user.has_perm('product.change_location'):
            return Response(
                {
                    'error': 'Permission denied',
                    'detail': 'You do not have permission to edit locations.',
                },
                status=status.HTTP_403_FORBIDDEN
            )
        return super().put(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_summary='Patch location',
        operation_description='Partially update a location by id.',
        request_body=LocationSerializer,
        responses={200: LocationSerializer},
        tags=['Product'],
    )
    def patch(self, request, *args, **kwargs):
        user = getattr(self.request, 'user', None)
        if not user.has_perm('product.change_location'):
            return Response(
                {
                    'error': 'Permission denied',
                    'detail': 'You do not have permission to edit locations.',
                },
                status=status.HTTP_403_FORBIDDEN
            )
        return super().patch(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_summary='Delete location',
        operation_description='Delete a location by id.',
        responses={204: 'No Content'},
        tags=['Product'],
    )
    def delete(self, request, *args, **kwargs):
        user = getattr(self.request, 'user', None)
        if not user.has_perm('product.delete_location'):
            return Response(
                {
                    'error': 'Permission denied',
                    'detail': 'You do not have permission to delete locations.',
                },
                status=status.HTTP_403_FORBIDDEN
            )
        return super().delete(request, *args, **kwargs)


class LocationListView(generics.ListAPIView):
    serializer_class = LocationSerializer
    queryset = Location.objects.all().select_related('parent')
    permission_classes = [drf_permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        q = (self.request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(location__icontains=q)
        return qs.order_by('location')

    @swagger_auto_schema(
        operation_summary='List locations',
        operation_description='List locations. Supports optional `q` search over location name.',
        responses={200: LocationSerializer(many=True)},
        tags=['Product'],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class StockCreateView(generics.GenericAPIView, mixins.CreateModelMixin):
    serializer_class = StockSerializer
    queryset = Stock.objects.all()
    permission_classes = [drf_permissions.IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Create stock',
        operation_description='Create a stock entry for a part at a location.',
        request_body=StockSerializer,
        responses={201: StockSerializer},
        tags=['Product'],
    )
    def post(self, request: Request, *args, **kwargs):
        user = getattr(self.request, 'user', None)
        if not user.has_perm('product.add_stock'):
            return Response(
                {
                    'error': 'Permission denied',
                    'detail': 'You do not have permission to create stocks.',
                },
                status=status.HTTP_403_FORBIDDEN
            )
        return self.create(request, *args, **kwargs)


class StockRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = StockSerializer
    queryset = Stock.objects.all()
    permission_classes = [drf_permissions.IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Get stock',
        operation_description='Retrieve a stock entry by id.',
        responses={200: StockSerializer},
        tags=['Product'],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_summary='Update stock',
        operation_description='Update a stock entry by id. Note: balance cannot be edited after creation.',
        request_body=StockSerializer,
        responses={200: StockSerializer},
        tags=['Product'],
    )
    def put(self, request, *args, **kwargs):
        user = getattr(self.request, 'user', None)
        if not user.has_perm('product.change_stock'):
            return Response(
                {
                    'error': 'Permission denied',
                    'detail': 'You do not have permission to edit stocks.',
                },
                status=status.HTTP_403_FORBIDDEN
            )
        return super().put(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_summary='Patch stock',
        operation_description='Partially update a stock entry by id. Note: balance cannot be edited after creation.',
        request_body=StockSerializer,
        responses={200: StockSerializer},
        tags=['Product'],
    )
    def patch(self, request, *args, **kwargs):
        user = getattr(self.request, 'user', None)
        if not user.has_perm('product.change_stock'):
            return Response(
                {
                    'error': 'Permission denied',
                    'detail': 'You do not have permission to edit stocks.',
                },
                status=status.HTTP_403_FORBIDDEN
            )
        return super().patch(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_summary='Delete stock',
        operation_description='Delete a stock entry by id.',
        responses={204: 'No Content'},
        tags=['Product'],
    )
    def delete(self, request, *args, **kwargs):
        user = getattr(self.request, 'user', None)
        if not user.has_perm('product.delete_stock'):
            return Response(
                {
                    'error': 'Permission denied',
                    'detail': 'You do not have permission to delete stocks.',
                },
                status=status.HTTP_403_FORBIDDEN
            )
        return super().delete(request, *args, **kwargs)


class StockListView(generics.ListAPIView):
    serializer_class = StockSerializer
    permission_classes = [drf_permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Stock.objects.all().select_related('location')
        q = (self.request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(part_name__icontains=q) | Q(part_number__icontains=q))
        return qs.order_by('part_number')

    @swagger_auto_schema(
        operation_summary='List stock',
        operation_description='List stock entries. Supports optional `q` search over part name/number.',
        responses={200: StockSerializer(many=True)},
        tags=['Product'],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


# Create your views here.
