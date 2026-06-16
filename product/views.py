import csv
import io
from rest_framework import status, generics, mixins, permissions as drf_permissions
from .models import Location, Stock
from .serializers import LocationSerializer, StockSerializer
from rest_framework.response import Response
from rest_framework.request import Request
from rest_framework.pagination import PageNumberPagination
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from django.db.models import Q
from django.db import transaction
from rest_framework.parsers import MultiPartParser, FormParser
from roles.permissions import IsSuperuserOnly


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


class LocationImportCSVView(generics.GenericAPIView):
    permission_classes = [drf_permissions.IsAuthenticated, IsSuperuserOnly]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        operation_summary='Import locations from CSV',
        operation_description='Upload a CSV file with headers `location,parent` to create nested locations. Superuser-only. Set `dry_run=true` to validate without writing.',
        manual_parameters=[
            openapi.Parameter('file', openapi.IN_FORM, type=openapi.TYPE_FILE, required=True),
            openapi.Parameter('dry_run', openapi.IN_FORM, type=openapi.TYPE_BOOLEAN, required=False),
        ],
        responses={200: openapi.Schema(type=openapi.TYPE_OBJECT)},
        tags=['Product'],
    )
    def post(self, request: Request, *args, **kwargs):
        upload = request.FILES.get('file')
        if upload is None:
            return Response(
                {'error': 'Invalid request', 'detail': 'Missing file.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        dry_raw = request.data.get('dry_run', False)
        dry_run = str(dry_raw).strip().lower() in {'1', 'true', 'yes', 'on'}

        try:
            raw = upload.read()
            text = raw.decode('utf-8-sig')
        except Exception:
            return Response(
                {'error': 'Invalid file', 'detail': 'Could not read CSV as UTF-8.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        reader = csv.DictReader(io.StringIO(text))
        header_by_norm = {}
        for h in (reader.fieldnames or []):
            norm = (h or '').strip().lower()
            if norm and norm not in header_by_norm:
                header_by_norm[norm] = h

        location_key = header_by_norm.get('location')
        if not location_key:
            return Response(
                {'error': 'Invalid CSV', 'detail': 'CSV must include a `location` header.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        parent_key = None
        for candidate in ('parent', 'parent_location'):
            k = header_by_norm.get(candidate)
            if k:
                parent_key = k
                break

        if parent_key is None:
            return Response(
                {'error': 'Invalid CSV', 'detail': 'CSV must include a `parent` header (or `parent_location`).'},
                status=status.HTTP_400_BAD_REQUEST
            )

        created = 0
        existed = 0
        total_rows = 0
        errors = []

        _MISSING = object()

        class DryParent:
            def __init__(self, name: str):
                self.name = name
                self.id = f'dry:{name}'

        parent_cache = {}
        child_cache = {}

        def get_top_level_location(name: str):
            cached = parent_cache.get(name, _MISSING)
            if cached is not _MISSING:
                return cached
            qs = Location.objects.filter(location=name)
            top = list(qs.filter(parent__isnull=True)[:2])
            if len(top) == 1:
                parent_cache[name] = top[0]
                return top[0]
            if len(top) > 1:
                raise ValueError(f'Ambiguous top-level location name: {name}')
            if qs.exists():
                raise ValueError(f'Location name exists but is not top-level: {name}')
            parent_cache[name] = None
            return None

        def get_or_create_location(name: str, parent_obj):
            nonlocal created, existed
            key = (str(getattr(parent_obj, 'id', 'null')), name)
            cached = child_cache.get(key, _MISSING)
            if cached is not _MISSING:
                if cached:
                    existed += 1
                else:
                    created += 1
                return

            if isinstance(parent_obj, DryParent):
                child_cache[key] = False
                created += 1
                return

            obj = Location.objects.filter(location=name, parent=parent_obj).first()
            if obj is not None:
                child_cache[key] = True
                existed += 1
                return

            if dry_run:
                child_cache[key] = False
                created += 1
                return

            Location.objects.create(location=name, parent=parent_obj)
            child_cache[key] = False
            created += 1

        with transaction.atomic():
            for idx, row in enumerate(reader, start=2):
                if row is None:
                    continue
                if not any(str(v).strip() for v in row.values() if v is not None):
                    continue

                total_rows += 1
                location_name = (row.get(location_key) or '').strip()
                parent_name = (row.get(parent_key) or '').strip()

                if not location_name:
                    errors.append({'row': idx, 'error': 'Missing location', 'data': row})
                    continue

                parent_obj = None
                if parent_name:
                    try:
                        parent_obj = get_top_level_location(parent_name)
                        if parent_obj is None:
                            if dry_run:
                                created += 1
                                parent_obj = DryParent(parent_name)
                                parent_cache[parent_name] = parent_obj
                            else:
                                parent_obj = Location.objects.create(location=parent_name, parent=None)
                                parent_cache[parent_name] = parent_obj
                    except ValueError as e:
                        errors.append({'row': idx, 'error': str(e), 'data': row})
                        continue

                try:
                    get_or_create_location(location_name, parent_obj)
                except Exception:
                    errors.append({'row': idx, 'error': 'Failed to create location', 'data': row})

            if errors or dry_run:
                transaction.set_rollback(True)

        if errors:
            return Response(
                {
                    'dry_run': dry_run,
                    'total_rows': total_rows,
                    'created': created,
                    'existed': existed,
                    'errors': errors,
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            {
                'dry_run': dry_run,
                'total_rows': total_rows,
                'created': created,
                'existed': existed,
                'errors': [],
            },
            status=status.HTTP_200_OK
        )


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
    class Pagination(PageNumberPagination):
        page_size = 50
        page_size_query_param = "page_size"
        max_page_size = 200

    pagination_class = Pagination

    def get_queryset(self):
        qs = Stock.objects.all().select_related('location')
        q = (self.request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(part_name__icontains=q) | Q(part_number__icontains=q))
        return qs.order_by('part_number')

    def list(self, request, *args, **kwargs):
        wants_pagination = "page" in request.query_params or "page_size" in request.query_params
        queryset = self.filter_queryset(self.get_queryset())
        if wants_pagination:
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @swagger_auto_schema(
        operation_summary='List stock',
        operation_description='List stock entries. Supports optional `q` search over part name/number. Supports pagination with `page` and `page_size`.',
        responses={200: StockSerializer(many=True)},
        tags=['Product'],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


# Create your views here.
