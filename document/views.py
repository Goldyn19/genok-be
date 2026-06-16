# apps/purchases/views.py (Corrected version)

from rest_framework import viewsets, status, permissions as drf_permissions, generics
from rest_framework.pagination import PageNumberPagination
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from django.db.models import Q, Sum, Count, F
from django.utils import timezone
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from .models import PurchaseBook, PurchaseApproval, SalesOrderItem, SalesApproval
from .serializers import (
    PurchaseBookListSerializer, PurchaseBookDetailSerializer,
    PurchaseBookCreateSerializer, PurchaseBookUpdateSerializer,
    PurchaseApprovalSerializer, PurchaseApproveSerializer,
    PurchaseRejectSerializer, ApprovalChainUpdateSerializer,
    PurchaseFilterSerializer, PurchaseDashboardSerializer,
    SalesOrderItemSerializer, SalesApprovalSerializer,
    SalesApproveSerializer, SalesRejectSerializer
)
from .services import PurchaseApprovalService
from roles.permissions import IsAdminOrReadOnly, IsRoleManager
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
import logging

logger = logging.getLogger(__name__)


# ==================== PurchaseBook ViewSet ====================

class PurchaseBookViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing purchase books with approval workflow.

    This ViewSet provides CRUD operations for purchase requests and 
    manages the 5-step approval workflow. Each approval step requires
    a specific permission.
    """
    queryset = PurchaseBook.objects.select_related(
        'location', 'created_by', 'stock'
    ).prefetch_related('approvals', 'approvals__approved_by')

    class Pagination(PageNumberPagination):
        page_size = 10
        page_size_query_param = "page_size"
        max_page_size = 100

    pagination_class = Pagination

    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'list':
            return PurchaseBookListSerializer
        elif self.action == 'create':
            return PurchaseBookCreateSerializer
        elif self.action == 'update' or self.action == 'partial_update':
            return PurchaseBookUpdateSerializer
        elif self.action == 'retrieve':
            return PurchaseBookDetailSerializer
        return PurchaseBookDetailSerializer

    def get_permissions(self):
        """Custom permissions based on action"""
        if self.action == 'create':
            permission_classes = [drf_permissions.IsAuthenticated]
        elif self.action in ['update', 'partial_update']:
            permission_classes = [drf_permissions.IsAuthenticated]
        elif self.action == 'destroy':
            permission_classes = [drf_permissions.IsAuthenticated, IsRoleManager]
        else:
            permission_classes = [drf_permissions.IsAuthenticated]
        return [permission() for permission in permission_classes]

    def get_queryset(self):
        """Filter queryset based on user permissions and query params"""
        """
        IMPORTANT: During Swagger schema generation, Django passes AnonymousUser.
        We must detect this and return the base queryset.
        """
        # CRITICAL FIX: Detect Swagger schema generation
        if getattr(self, 'swagger_fake_view', False):
            return self.queryset

        queryset = self.queryset

        # Get the current user
        user = getattr(self.request, 'user', None)

        # If no user or anonymous, return empty queryset for non-schema requests
        if not user or not user.is_authenticated:
            return queryset.none()

        # Apply user permission filtering
        if not user.has_perm('document.can_view_all_purchases'):
            queryset = queryset.filter(created_by=user)

        filter_serializer = PurchaseFilterSerializer(data=self.request.query_params)
        if filter_serializer.is_valid():
            filters = filter_serializer.validated_data
            if filters.get('status'):
                queryset = queryset.filter(status=filters['status'])
            if filters.get('location_id'):
                queryset = queryset.filter(location_id=filters['location_id'])
            if filters.get('created_by_id'):
                queryset = queryset.filter(created_by_id=filters['created_by_id'])
            if filters.get('from_date'):
                queryset = queryset.filter(created_at__date__gte=filters['from_date'])
            if filters.get('to_date'):
                queryset = queryset.filter(created_at__date__lte=filters['to_date'])
            if filters.get('search'):
                search_term = filters['search']
                queryset = queryset.filter(
                    Q(name__icontains=search_term) |
                    Q(part_number__icontains=search_term) |
                    Q(created_by__email__icontains=search_term) |
                    Q(created_by__first_name__icontains=search_term) |
                    Q(created_by__last_name__icontains=search_term)
                )
        return queryset

    def get_object(self):
        """
        Allow approvers to open and act on purchases that are currently assigned
        to them even if they don't have the general "view all purchases" permission.
        """
        try:
            return super().get_object()
        except Http404:
            if getattr(self, 'swagger_fake_view', False):
                raise

            user = getattr(self.request, 'user', None)
            if not user or not user.is_authenticated:
                raise

            if self.action not in ['retrieve', 'approve', 'reject', 'approval_status']:
                raise

            lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
            lookup_value = self.kwargs.get(lookup_url_kwarg)
            if lookup_value is None:
                raise

            obj = self.queryset.filter(**{self.lookup_field: lookup_value}).first()
            if obj is None:
                raise

            if not (obj.can_approve(user) or obj.can_reject(user)):
                raise

            self.check_object_permissions(self.request, obj)
            return obj

    # ==================== LIST ====================

    @swagger_auto_schema(
        operation_summary="List purchases",
        operation_description="Returns a list of purchases. Users see only their own purchases unless they have 'can_view_all_purchases' permission.",
        manual_parameters=[
            openapi.Parameter('status', openapi.IN_QUERY, description="Filter by status", type=openapi.TYPE_STRING,
                              enum=['pending', 'approved', 'confirmed', 'failed']),
            openapi.Parameter('location_id', openapi.IN_QUERY, description="Filter by location ID",
                              type=openapi.TYPE_INTEGER),
            openapi.Parameter('created_by_id', openapi.IN_QUERY, description="Filter by creator user ID",
                              type=openapi.TYPE_INTEGER),
            openapi.Parameter('from_date', openapi.IN_QUERY, description="Filter from date (YYYY-MM-DD)",
                              type=openapi.TYPE_STRING, format='date'),
            openapi.Parameter('to_date', openapi.IN_QUERY, description="Filter to date (YYYY-MM-DD)",
                              type=openapi.TYPE_STRING, format='date'),
            openapi.Parameter('search', openapi.IN_QUERY,
                              description="Search by name, part number, or creator username", type=openapi.TYPE_STRING),
            openapi.Parameter('page', openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, description="Items per page", type=openapi.TYPE_INTEGER),
        ],
        responses={
            200: PurchaseBookListSerializer(many=True),
            401: "Authentication required",
            403: "Permission denied"
        },
        tags=['Purchases']
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    # ==================== CREATE ====================

    @swagger_auto_schema(
        operation_summary="Create purchase",
        operation_description="Creates a new purchase request. Automatically creates 5 approval steps.",
        request_body=PurchaseBookCreateSerializer,
        responses={
            201: PurchaseBookDetailSerializer,
            400: "Validation error (invalid quantity, price, or missing fields)",
            401: "Authentication required"
        },
        tags=['Purchases']
    )
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    # ==================== RETRIEVE ====================

    @swagger_auto_schema(
        operation_summary="Get purchase details",
        operation_description="Returns detailed information about a specific purchase including approval chain status.",
        responses={
            200: PurchaseBookDetailSerializer,
            404: "Purchase not found",
            403: "Permission denied (not owner and no view_all permission)"
        },
        tags=['Purchases']
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    # ==================== UPDATE ====================

    @swagger_auto_schema(
        operation_summary="Update purchase",
        operation_description="Updates a purchase. Only allowed before any approvals have been made.",
        request_body=PurchaseBookUpdateSerializer,
        responses={
            200: PurchaseBookUpdateSerializer,
            400: "Cannot update after approvals started",
            403: "Permission denied",
            404: "Purchase not found"
        },
        tags=['Purchases']
    )
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_summary="Partially update purchase",
        operation_description="Partially updates a purchase. Only allowed before any approvals have been made.",
        request_body=PurchaseBookUpdateSerializer,
        responses={
            200: PurchaseBookUpdateSerializer,
            400: "Cannot update after approvals started",
            403: "Permission denied",
            404: "Purchase not found"
        },
        tags=['Purchases']
    )
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    def perform_update(self, serializer):
        purchase = serializer.instance
        if purchase.created_by != self.request.user:
            raise PermissionDenied("Only the creator can edit this purchase")
        serializer.save()

    # ==================== DESTROY ====================

    @swagger_auto_schema(
        operation_summary="Delete purchase",
        operation_description="Deletes a purchase. Only allowed before any approvals have been made.",
        responses={
            204: "Purchase deleted successfully",
            403: "Permission denied (requires role manager)",
            404: "Purchase not found"
        },
        tags=['Purchases']
    )
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    # ==================== APPROVE ====================

    @swagger_auto_schema(
        operation_summary="Approve current step",
        operation_description="""
        Approves the current pending approval step for a purchase.

        **Approval Flow:**
        - Step 1: Requires 'purchases.can_do_first_approval' permission
        - Step 2: Requires 'purchases.can_do_second_approval' permission
        - Step 3: Requires 'purchases.can_do_third_approval' permission
        - Step 4: Requires 'purchases.can_do_fourth_approval' permission
        - Step 5: Requires 'purchases.can_do_final_approval' permission

        **Rules:**
        - Cannot approve your own purchase
        - Approvals must happen in sequence (step 1 → step 2 → ...)
        - After all 5 approvals, stock is automatically updated
        """,
        request_body=PurchaseApproveSerializer,
        responses={
            200: openapi.Response(
                description="Approval successful",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'message': openapi.Schema(type=openapi.TYPE_STRING),
                        'purchase_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'status': openapi.Schema(type=openapi.TYPE_STRING),
                        'current_step': openapi.Schema(type=openapi.TYPE_INTEGER, nullable=True),
                        'approval_progress': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'approvals': openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(type=openapi.TYPE_OBJECT)
                        )
                    }
                )
            ),
            400: "Validation error (cannot approve out of order)",
            403: "Permission denied (missing required permission or self-approval)",
            404: "Purchase not found"
        },
        tags=['Approvals']
    )
    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        """Approve the current step of a purchase."""
        purchase = self.get_object()
        serializer = PurchaseApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            approval = purchase.approve(
                user=request.user,
                reason=serializer.validated_data.get('reason'),
                location_override=serializer.validated_data.get('location')
            )

            response_data = {
                'message': f'Step {approval.sequence} approved successfully',
                'purchase_id': purchase.id,
                'status': purchase.status,
                'current_step': purchase.current_step_number,
                'approval_progress': purchase.approval_progress,
                'approvals': PurchaseApprovalSerializer(
                    purchase.approvals.all(), many=True
                ).data
            }

            return Response(response_data, status=status.HTTP_200_OK)

        except PermissionDenied as e:
            return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as e:
            return Response({'error': e.messages}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception(f"Approval failed for purchase {purchase.id}")
            return Response({'error': 'An unexpected error occurred'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # ==================== REJECT ====================

    @swagger_auto_schema(
        operation_summary="Reject purchase",
        operation_description="""
        Rejects a purchase at the current approval step.

        **Requirements:**
        - User must have 'purchases.can_reject_purchase' permission
        - User must be the current approver for the pending step
        - A rejection reason is required

        **Effect:**
        - Purchase status becomes 'failed'
        - Current approval step status becomes 'failed'
        - Stock is NOT updated
        """,
        request_body=PurchaseRejectSerializer,
        responses={
            200: openapi.Response(
                description="Rejection successful",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'message': openapi.Schema(type=openapi.TYPE_STRING),
                        'purchase_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'status': openapi.Schema(type=openapi.TYPE_STRING),
                        'reason': openapi.Schema(type=openapi.TYPE_STRING)
                    }
                )
            ),
            400: "Validation error (reason required)",
            403: "Permission denied (missing reject permission or not the approver)",
            404: "Purchase not found"
        },
        tags=['Approvals']
    )
    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        """Reject the purchase at current step."""
        purchase = self.get_object()
        serializer = PurchaseRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            purchase.reject(
                user=request.user,
                reason=serializer.validated_data['reason']
            )

            return Response({
                'message': f'Purchase #{purchase.id} has been rejected',
                'purchase_id': purchase.id,
                'status': purchase.status,
                'reason': serializer.validated_data['reason']
            }, status=status.HTTP_200_OK)

        except PermissionDenied as e:
            return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)
        except Exception as e:
            logger.exception(f"Rejection failed for purchase {purchase.id}")
            return Response({'error': 'An unexpected error occurred'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # ==================== APPROVAL STATUS ====================

    @swagger_auto_schema(
        operation_summary="Get approval status",
        operation_description="""
        Returns detailed approval chain status for a purchase.

        **Information returned:**
        - Current step and progress percentage
        - Each step's status (pending/confirmed/failed)
        - Who approved each step and when
        - Whether current user can approve or reject
        """,
        responses={
            200: openapi.Response(
                description="Approval status retrieved",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'purchase_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'status': openapi.Schema(type=openapi.TYPE_STRING),
                        'progress_percentage': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'current_step': openapi.Schema(type=openapi.TYPE_INTEGER, nullable=True),
                        'current_required_permission': openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
                        'total_amount': openapi.Schema(type=openapi.TYPE_NUMBER),
                        'created_by': openapi.Schema(type=openapi.TYPE_STRING),
                        'created_at': openapi.Schema(type=openapi.TYPE_STRING, format='date-time'),
                        'approval_chain': openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(type=openapi.TYPE_OBJECT)
                        ),
                        'can_approve': openapi.Schema(type=openapi.TYPE_BOOLEAN),
                        'can_reject': openapi.Schema(type=openapi.TYPE_BOOLEAN)
                    }
                )
            ),
            403: "Permission denied",
            404: "Purchase not found"
        },
        tags=['Approvals']
    )
    @action(detail=True, methods=['get'], url_path='approval-status')
    def approval_status(self, request, pk=None):
        """Get detailed approval status for a purchase."""
        purchase = self.get_object()

        can_access_status = (
            purchase.created_by == request.user
            or request.user.has_perm('document.can_view_all_purchases')
            or purchase.can_approve(request.user)
            or purchase.can_reject(request.user)
        )
        if not can_access_status:
            return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

        chain_status = purchase.get_approval_chain_status()

        if not request.user.has_perm('purchases.view_permission_details'):
            for step in chain_status:
                step.pop('required_permission', None)

        return Response({
            'purchase_id': purchase.id,
            'status': purchase.status,
            'progress_percentage': purchase.approval_progress,
            'current_step': purchase.current_step_number,
            'current_required_permission': purchase.current_required_permission,
            'total_amount': purchase.total_amount,
            'created_by': purchase.created_by.username,
            'created_at': purchase.created_at,
            'approval_chain': chain_status,
            'can_approve': purchase.can_approve(request.user),
            'can_reject': purchase.can_reject(request.user)
        })

    # ==================== MY PURCHASES ====================

    @swagger_auto_schema(
        operation_summary="Get my purchases",
        operation_description="Returns all purchases created by the current authenticated user.",
        manual_parameters=[
            openapi.Parameter('status', openapi.IN_QUERY, description="Filter by status", type=openapi.TYPE_STRING,
                              enum=['pending', 'approved', 'confirmed', 'failed']),
            openapi.Parameter('search', openapi.IN_QUERY, description="Search by name, part number, status, or location", type=openapi.TYPE_STRING),
        ],
        responses={
            200: PurchaseBookListSerializer(many=True),
            401: "Authentication required"
        },
        tags=['Purchases']
    )
    @action(detail=False, methods=['get'], url_path='my-purchases')
    def my_purchases(self, request):
        """Get purchases created by the current user."""
        queryset = PurchaseBook.objects.filter(
            created_by=request.user
        ).select_related('location', 'stock')

        status_filter = request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        search = (request.query_params.get('search') or '').strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(part_number__icontains=search) |
                Q(status__icontains=search) |
                Q(location__location__icontains=search)
            )

        wants_pagination = "page" in request.query_params or "page_size" in request.query_params
        if wants_pagination:
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = PurchaseBookListSerializer(page, many=True)
                return self.get_paginated_response(serializer.data)

        serializer = PurchaseBookListSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='my-actions')
    def my_actions(self, request):
        user = request.user
        search = (request.query_params.get('search') or '').strip().lower()

        purchase_actions = (
            PurchaseApproval.objects.select_related('purchase')
            .prefetch_related('purchase__approvals')
            .filter(approved_by=user)
            .exclude(status='pending')
        )
        sales_actions = (
            SalesApproval.objects.select_related('sales_order')
            .prefetch_related('sales_order__approvals')
            .filter(approved_by=user)
            .exclude(status='pending')
        )

        rows = []

        for a in purchase_actions:
            purchase = a.purchase
            approvals = list(purchase.approvals.all())
            total_steps = max((x.sequence for x in approvals), default=a.sequence)
            has_later_actions = any(x.sequence > a.sequence and x.status in ('confirmed', 'failed') for x in approvals)
            is_final_step = a.sequence == total_steps
            is_finalized = purchase.status in ('approved', 'confirmed')

            can_change = not is_final_step and not is_finalized and not has_later_actions
            blocked_reason = None
            if is_final_step:
                blocked_reason = "Final approval cannot be revoked"
            elif is_finalized:
                blocked_reason = "Purchase already finalized"
            elif has_later_actions:
                blocked_reason = "Next step already actioned"

            decision = 'approved' if a.status == 'confirmed' else 'rejected'
            row = {
                'type': 'purchase',
                'approval_id': a.id,
                'object_id': purchase.id,
                'step': a.sequence,
                'decision': decision,
                'reason': a.reason,
                'acted_at': a.approved_at,
                'current_status': purchase.status,
                'name': purchase.name,
                'part_number': purchase.part_number,
                'quantity': purchase.quantity,
                'can_change_decision': can_change,
                'blocked_reason': blocked_reason,
            }

            if search:
                hay = ' '.join(
                    [
                        str(row.get('object_id') or ''),
                        row.get('name') or '',
                        row.get('part_number') or '',
                        row.get('decision') or '',
                        row.get('current_status') or '',
                        row.get('reason') or '',
                    ]
                ).lower()
                if search not in hay:
                    continue

            rows.append(row)

        for a in sales_actions:
            item = a.sales_order
            approvals = list(item.approvals.all())
            total_steps = max((x.sequence for x in approvals), default=a.sequence)
            has_later_actions = any(x.sequence > a.sequence and x.status in ('confirmed', 'failed') for x in approvals)
            is_final_step = a.sequence == total_steps
            is_finalized = item.status == 'approved'

            can_change = not is_final_step and not is_finalized and not has_later_actions
            blocked_reason = None
            if is_final_step:
                blocked_reason = "Final approval cannot be revoked"
            elif is_finalized:
                blocked_reason = "Sale already finalized"
            elif has_later_actions:
                blocked_reason = "Next step already actioned"

            decision = 'approved' if a.status == 'confirmed' else 'rejected'
            row = {
                'type': 'sale',
                'approval_id': a.id,
                'object_id': item.id,
                'step': a.sequence,
                'decision': decision,
                'reason': a.reason,
                'acted_at': a.approved_at,
                'current_status': item.status,
                'name': item.part_name,
                'part_number': item.part_number,
                'quantity': item.quantity,
                'can_change_decision': can_change,
                'blocked_reason': blocked_reason,
            }

            if search:
                hay = ' '.join(
                    [
                        str(row.get('object_id') or ''),
                        row.get('name') or '',
                        row.get('part_number') or '',
                        row.get('decision') or '',
                        row.get('current_status') or '',
                        row.get('reason') or '',
                    ]
                ).lower()
                if search not in hay:
                    continue

            rows.append(row)

        rows.sort(key=lambda r: (r.get('acted_at') is None, r.get('acted_at')), reverse=True)

        wants_pagination = "page" in request.query_params or "page_size" in request.query_params
        if wants_pagination:
            page = self.paginate_queryset(rows)
            if page is not None:
                return self.get_paginated_response(page)

        return Response(rows)

    @action(detail=False, methods=['post'], url_path='revoke-action')
    def revoke_action(self, request):
        action_type = request.data.get('type')
        approval_id = request.data.get('approval_id')

        if action_type not in ('purchase', 'sale'):
            return Response({'error': 'Invalid type'}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(approval_id, int):
            try:
                approval_id = int(str(approval_id))
            except Exception:
                return Response({'error': 'Invalid approval_id'}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user

        if action_type == 'purchase':
            try:
                approval = (
                    PurchaseApproval.objects.select_related('purchase')
                    .prefetch_related('purchase__approvals')
                    .get(id=approval_id, approved_by=user)
                )
            except PurchaseApproval.DoesNotExist:
                return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

            purchase = approval.purchase
            approvals = list(purchase.approvals.all())
            total_steps = max((x.sequence for x in approvals), default=approval.sequence)
            if approval.sequence == total_steps:
                return Response({'error': 'Final approval cannot be revoked'}, status=status.HTTP_400_BAD_REQUEST)
            if purchase.status in ('approved', 'confirmed'):
                return Response({'error': 'Purchase already finalized'}, status=status.HTTP_400_BAD_REQUEST)
            if approval.status not in ('confirmed', 'failed'):
                return Response({'error': 'Nothing to revoke'}, status=status.HTTP_400_BAD_REQUEST)
            if any(x.sequence > approval.sequence and x.status in ('confirmed', 'failed') for x in approvals):
                return Response({'error': 'Next step already actioned'}, status=status.HTTP_400_BAD_REQUEST)

            previous_status = approval.status

            with transaction.atomic():
                approval.status = 'pending'
                approval.reason = None
                approval.approved_at = None
                approval.approved_by = None
                approval.save(update_fields=['status', 'reason', 'approved_at', 'approved_by'])

                if previous_status == 'failed' and purchase.status == 'failed':
                    purchase.status = 'pending'
                    purchase.save(update_fields=['status'])

            return Response({'ok': True}, status=status.HTTP_200_OK)

        try:
            approval = (
                SalesApproval.objects.select_related('sales_order')
                .prefetch_related('sales_order__approvals')
                .get(id=approval_id, approved_by=user)
            )
        except SalesApproval.DoesNotExist:
            return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

        item = approval.sales_order
        approvals = list(item.approvals.all())
        total_steps = max((x.sequence for x in approvals), default=approval.sequence)
        if approval.sequence == total_steps:
            return Response({'error': 'Final approval cannot be revoked'}, status=status.HTTP_400_BAD_REQUEST)
        if item.status == 'approved':
            return Response({'error': 'Sale already finalized'}, status=status.HTTP_400_BAD_REQUEST)
        if approval.status not in ('confirmed', 'failed'):
            return Response({'error': 'Nothing to revoke'}, status=status.HTTP_400_BAD_REQUEST)
        if any(x.sequence > approval.sequence and x.status in ('confirmed', 'failed') for x in approvals):
            return Response({'error': 'Next step already actioned'}, status=status.HTTP_400_BAD_REQUEST)

        previous_status = approval.status

        with transaction.atomic():
            approval.status = 'pending'
            approval.reason = None
            approval.approved_at = None
            approval.approved_by = None
            approval.save(update_fields=['status', 'reason', 'approved_at', 'approved_by'])

            if previous_status == 'failed' and item.status == 'rejected':
                item.status = 'pending'
                item.save(update_fields=['status'])

            item.update_parent_order_status()

        return Response({'ok': True}, status=status.HTTP_200_OK)

    # ==================== PENDING APPROVALS ====================

    @swagger_auto_schema(
        operation_summary="Get pending approvals",
        operation_description="Returns all purchases that are pending approval from the current user.",
        responses={
            200: PurchaseBookListSerializer(many=True),
            401: "Authentication required"
        },
        tags=['Approvals']
    )
    @action(detail=False, methods=['get'], url_path='pending-approvals')
    def pending_approvals(self, request):
        """Get purchases pending approval from the current user."""
        user = request.user
        search = (request.query_params.get('search') or '').strip()

        purchases = PurchaseBook.objects.filter(
            status='pending',
            approvals__status='pending',
            approvals__sequence=F('approvals__sequence')
        ).filter(
            id__in=self.queryset.filter(status='pending').values_list('id', flat=True)
        ).select_related('location', 'created_by')

        can_approve_ids = []
        for purchase in purchases:
            if purchase.can_approve(user):
                can_approve_ids.append(purchase.id)

        queryset = PurchaseBook.objects.filter(id__in=can_approve_ids)
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(part_number__icontains=search) |
                Q(status__icontains=search) |
                Q(created_by__email__icontains=search) |
                Q(created_by__first_name__icontains=search) |
                Q(created_by__last_name__icontains=search)
            )
        wants_pagination = "page" in request.query_params or "page_size" in request.query_params
        if wants_pagination:
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = PurchaseBookListSerializer(page, many=True)
                return self.get_paginated_response(serializer.data)

        serializer = PurchaseBookListSerializer(queryset, many=True)
        return Response(serializer.data)

    # ==================== REGENERATE CHAIN ====================

    @swagger_auto_schema(
        operation_summary="Regenerate approval chain",
        operation_description="[ADMIN ONLY] Regenerates the 5-step approval chain for a purchase. Only superusers can perform this action.",
        request_body=ApprovalChainUpdateSerializer,
        responses={
            200: openapi.Response(
                description="Chain regenerated",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'message': openapi.Schema(type=openapi.TYPE_STRING),
                        'purchase_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'num_steps': openapi.Schema(type=openapi.TYPE_INTEGER)
                    }
                )
            ),
            400: "Cannot regenerate after approvals started",
            403: "Only superusers can perform this action",
            404: "Purchase not found"
        },
        tags=['Admin']
    )
    @action(detail=True, methods=['post'], url_path='regenerate-chain')
    def regenerate_chain(self, request, pk=None):
        """Regenerate approval chain for a purchase (admin only)."""
        if not request.user.is_superuser:
            return Response(
                {'error': 'Only superusers can regenerate approval chains'},
                status=status.HTTP_403_FORBIDDEN
            )

        purchase = self.get_object()

        if purchase.approvals.filter(status='confirmed').exists():
            return Response(
                {'error': 'Cannot regenerate chain after approvals have started'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            purchase.approvals.all().delete()
            PurchaseApprovalService.create_approval_chain(purchase)

            return Response({
                'message': 'Approval chain regenerated with 5 steps',
                'purchase_id': purchase.id,
                'num_steps': 5
            })
        except ValidationError as e:
            return Response({'error': e.messages}, status=status.HTTP_400_BAD_REQUEST)

    # ==================== RETRY STOCK ====================

    @swagger_auto_schema(
        operation_summary="Retry stock finalization",
        operation_description="[ADMIN ONLY] Retries stock finalization for a purchase that failed to update stock after approval. Only superusers can perform this action.",
        responses={
            200: openapi.Response(
                description="Stock finalized",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'message': openapi.Schema(type=openapi.TYPE_STRING),
                        'purchase_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'status': openapi.Schema(type=openapi.TYPE_STRING)
                    }
                )
            ),
            400: "Cannot retry for purchases not in 'approved' status",
            403: "Only superusers can perform this action",
            404: "Purchase not found"
        },
        tags=['Admin']
    )
    @action(detail=True, methods=['post'], url_path='retry-stock')
    def retry_stock(self, request, pk=None):
        """Retry stock finalization for a purchase (admin only)."""
        if not request.user.is_superuser:
            return Response(
                {'error': 'Only superusers can retry stock finalization'},
                status=status.HTTP_403_FORBIDDEN
            )

        purchase = self.get_object()

        if purchase.status != 'approved':
            return Response(
                {'error': f'Cannot retry stock for purchase with status: {purchase.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        stock_updated = purchase.finalize_stock()

        if stock_updated:
            return Response({
                'message': 'Stock finalized successfully',
                'purchase_id': purchase.id,
                'status': purchase.status
            })
        else:
            return Response(
                {'error': 'Stock finalization failed again. Manual intervention required.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ==================== PurchaseApproval ViewSet ====================

class PurchaseApprovalViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing purchase approvals (read-only).
    Approvals are managed through the PurchaseBook approve/reject actions.
    """
    queryset = PurchaseApproval.objects.select_related(
        'purchase', 'approved_by', 'purchase__location', 'purchase__created_by'
    ).all()
    serializer_class = PurchaseApprovalSerializer
    permission_classes = [drf_permissions.IsAuthenticated]

    def get_queryset(self):
        """
        Filter queryset based on user permissions.

        IMPORTANT: During Swagger schema generation, self.request.user may be AnonymousUser.
        Use a flag to detect schema generation and return unfiltered queryset.
        """
        # CRITICAL FIX: Detect Swagger schema generation
        if getattr(self, 'swagger_fake_view', False):
            return self.queryset

        queryset = self.queryset
        user = self.request.user

        # If user is not authenticated, return empty queryset
        if not user or not user.is_authenticated:
            return queryset.none()

        purchase_id = self.request.query_params.get('purchase_id')
        if purchase_id:
            queryset = queryset.filter(purchase_id=purchase_id)

        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        approver_id = self.request.query_params.get('approver_id')
        if approver_id:
            queryset = queryset.filter(approved_by_id=approver_id)

        if not user.has_perm('purchases.can_view_all_purchases'):
            queryset = queryset.filter(
                Q(purchase__created_by=user) |
                Q(approved_by=user)
            )

        return queryset

    @swagger_auto_schema(
        operation_summary="List approvals",
        operation_description="Returns a list of purchase approvals. Can be filtered by purchase_id, status, or approver_id.",
        manual_parameters=[
            openapi.Parameter('purchase_id', openapi.IN_QUERY, description="Filter by purchase ID",
                              type=openapi.TYPE_INTEGER),
            openapi.Parameter('status', openapi.IN_QUERY, description="Filter by status", type=openapi.TYPE_STRING,
                              enum=['pending', 'confirmed', 'failed']),
            openapi.Parameter('approver_id', openapi.IN_QUERY, description="Filter by approver user ID",
                              type=openapi.TYPE_INTEGER),
            openapi.Parameter('page', openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER),
            openapi.Parameter('page_size', openapi.IN_QUERY, description="Items per page", type=openapi.TYPE_INTEGER),
        ],
        responses={
            200: PurchaseApprovalSerializer(many=True),
            401: "Authentication required"
        },
        tags=['Approvals']
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_summary="Get approval details",
        operation_description="Returns detailed information about a specific approval step.",
        responses={
            200: PurchaseApprovalSerializer,
            404: "Approval not found"
        },
        tags=['Approvals']
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)


# ==================== Dashboard View ====================

class PurchaseDashboardView(generics.GenericAPIView):
    """
    View for purchase dashboard statistics.
    Provides aggregated metrics for purchase and approval analytics.
    """
    permission_classes = [drf_permissions.IsAuthenticated, IsRoleManager]

    @swagger_auto_schema(
        operation_summary="Get dashboard statistics",
        operation_description="""
        Returns aggregated statistics for purchase dashboard.

        **Requires:** Role Manager permission

        **Statistics returned:**
        - Total purchases, pending approvals, confirmed, failed
        - Total value of confirmed purchases
        - Average approval time in hours
        - Approval distribution by status
        - Recent purchases (last 10)
        - Top locations by purchase value
        - Top creators by purchase value
        """,
        responses={
            200: PurchaseDashboardSerializer,
            401: "Authentication required",
            403: "Role Manager permission required"
        },
        tags=['Dashboard']
    )
    def get(self, request):
        """Get dashboard statistics"""
        queryset = PurchaseBook.objects.all()

        total_purchases = queryset.count()
        pending_approvals = queryset.filter(status='pending').count()
        confirmed_purchases = queryset.filter(status='confirmed').count()
        failed_purchases = queryset.filter(status='failed').count()

        total_value = queryset.filter(
            status='confirmed'
        ).aggregate(
            total=Sum(F('price') * F('quantity'), default=0)
        )['total']

        # Average approval time
        approval_times = []
        for purchase in queryset.filter(status='confirmed'):
            first_approval = purchase.approvals.filter(status='confirmed').order_by('sequence').first()
            if first_approval and first_approval.approved_at:
                approval_time = first_approval.approved_at - purchase.created_at
                approval_times.append(approval_time.total_seconds())

        avg_approval_seconds = sum(approval_times) / len(approval_times) if approval_times else 0

        approval_by_status = queryset.values('status').annotate(count=Count('id'))
        recent_purchases = queryset.select_related('location', 'created_by').order_by('-created_at')[:10]

        location_stats = queryset.filter(
            status='confirmed'
        ).values('location__location').annotate(
            total=Sum(F('price') * F('quantity'), default=0),
            count=Count('id')
        ).order_by('-total')[:5]

        creator_stats = queryset.filter(
            status='confirmed'
        ).values('created_by__email').annotate(
            total=Sum(F('price') * F('quantity'), default=0),
            count=Count('id')
        ).order_by('-total')[:5]

        return Response({
            'statistics': {
                'total_purchases': total_purchases,
                'pending_approvals': pending_approvals,
                'confirmed_purchases': confirmed_purchases,
                'failed_purchases': failed_purchases,
                'total_value': float(total_value) if total_value else 0,
                'average_approval_time_hours': round(avg_approval_seconds / 3600, 2) if avg_approval_seconds else None
            },
            'approval_by_status': list(approval_by_status),
            'recent_purchases': PurchaseBookListSerializer(recent_purchases, many=True).data,
            'top_locations': list(location_stats),
            'top_creators': list(creator_stats)
        })


# ==================== Sales Order Item Approval ====================

class SalesOrderItemViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SalesOrderItem.objects.select_related(
        'sales_order', 'sales_order__cart', 'product'
    ).prefetch_related('approvals', 'approvals__approved_by')
    serializer_class = SalesOrderItemSerializer
    permission_classes = [drf_permissions.IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return self.queryset

        queryset = self.queryset
        user = getattr(self.request, 'user', None)
        if not user or not user.is_authenticated:
            return queryset.none()

        if not user.has_perm('document.can_view_all_sales'):
            queryset = queryset.filter(sales_order__cart__user=user)

        status_filter = (self.request.query_params.get('status') or '').strip()
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        part_number = (self.request.query_params.get('part_number') or '').strip()
        if part_number:
            queryset = queryset.filter(product__part_number__icontains=part_number)

        q = (self.request.query_params.get('q') or '').strip()
        if q:
            queryset = queryset.filter(
                Q(product__part_number__icontains=q) |
                Q(product__part_name__icontains=q)
            )

        return queryset

    def _approval_progress(self, item: SalesOrderItem):
        total = item.approvals.count()
        completed = item.approvals.filter(status='confirmed').count()
        return int((completed / total) * 100) if total > 0 else 0

    def _current_step(self, item: SalesOrderItem):
        current = item.approvals.filter(status='pending').order_by('sequence').first()
        return current.sequence if current else None

    def _approval_chain_status(self, item: SalesOrderItem):
        chain = []
        approvals = item.approvals.select_related('approved_by').order_by('sequence')
        for approval in approvals:
            is_blocked = False
            if approval.sequence > 1:
                previous = item.approvals.filter(sequence=approval.sequence - 1).first()
                is_blocked = previous and previous.status != 'confirmed'

            required_perm = item.get_approval_permission_for_step(approval.sequence)

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

    @swagger_auto_schema(
        operation_summary="Approve current sales step",
        operation_description="Approves the current pending approval step for a sales order item.",
        request_body=SalesApproveSerializer,
        responses={200: openapi.Response(description="Approval successful")},
        tags=['Sales Approvals']
    )
    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, id=None):
        sales_item = self.get_object()
        serializer = SalesApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            approval = sales_item.approve(
                user=request.user,
                reason=serializer.validated_data.get('reason')
            )

            return Response({
                'message': f'Step {approval.sequence} approved successfully',
                'sales_item_id': str(sales_item.id),
                'status': sales_item.status,
                'current_step': self._current_step(sales_item),
                'approval_progress': self._approval_progress(sales_item),
                'approvals': SalesApprovalSerializer(sales_item.approvals.all(), many=True).data
            }, status=status.HTTP_200_OK)

        except PermissionDenied as e:
            return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as e:
            return Response({'error': e.messages}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception(f"Approval failed for sales item {sales_item.id}")
            return Response({'error': 'An unexpected error occurred'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        operation_summary="Reject sales item",
        operation_description="Rejects a sales order item at the current approval step.",
        request_body=SalesRejectSerializer,
        responses={200: openapi.Response(description="Rejection successful")},
        tags=['Sales Approvals']
    )
    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, id=None):
        sales_item = self.get_object()
        serializer = SalesRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            sales_item.reject(
                user=request.user,
                reason=serializer.validated_data['reason']
            )

            return Response({
                'message': f'Sales item {sales_item.id} has been rejected',
                'sales_item_id': str(sales_item.id),
                'status': sales_item.status,
                'reason': serializer.validated_data['reason']
            }, status=status.HTTP_200_OK)

        except PermissionDenied as e:
            return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as e:
            return Response({'error': e.messages}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception(f"Rejection failed for sales item {sales_item.id}")
            return Response({'error': 'An unexpected error occurred'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        operation_summary="Get sales item approval status",
        operation_description="Returns approval chain status and current user's ability to approve/reject for a sales order item.",
        responses={200: openapi.Response(description="Approval status retrieved")},
        tags=['Sales Approvals']
    )
    @action(detail=True, methods=['get'], url_path='approval-status')
    def approval_status(self, request, id=None):
        sales_item = self.get_object()
        return Response({
            'sales_item_id': str(sales_item.id),
            'status': sales_item.status,
            'progress_percentage': self._approval_progress(sales_item),
            'current_step': self._current_step(sales_item),
            'current_required_permission': sales_item.get_approval_permission_for_step(self._current_step(sales_item)) if self._current_step(sales_item) else None,
            'total_amount': sales_item.total_price,
            'created_at': sales_item.created_at,
            'approval_chain': self._approval_chain_status(sales_item),
            'can_approve': sales_item.can_approve(request.user),
            'can_reject': request.user.has_perm('document.can_reject_sale')
        })

    @swagger_auto_schema(
        operation_summary="Get my sales items",
        operation_description="Returns sales order items created from the current user's carts.",
        responses={200: SalesOrderItemSerializer(many=True)},
        tags=['Sales']
    )
    @action(detail=False, methods=['get'], url_path='my-sales')
    def my_sales(self, request):
        queryset = self.get_queryset().filter(sales_order__cart__user=request.user)
        serializer = SalesOrderItemSerializer(queryset, many=True)
        return Response(serializer.data)

    @swagger_auto_schema(
        operation_summary="Get pending sales approvals",
        operation_description="Returns sales order items pending approval from the current user.",
        responses={200: SalesOrderItemSerializer(many=True)},
        tags=['Sales Approvals']
    )
    @action(detail=False, methods=['get'], url_path='pending-approvals')
    def pending_approvals(self, request):
        user = request.user
        queryset = self.queryset.filter(status='pending', approvals__status='pending').distinct()
        can_approve_ids = []
        for item in queryset:
            if item.can_approve(user):
                can_approve_ids.append(item.id)
        items = self.queryset.filter(id__in=can_approve_ids)
        serializer = SalesOrderItemSerializer(items, many=True)
        return Response(serializer.data)


class SalesApprovalViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SalesApproval.objects.select_related(
        'sales_order', 'sales_order__sales_order', 'approved_by'
    ).all()
    serializer_class = SalesApprovalSerializer
    permission_classes = [drf_permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return self.queryset

        queryset = self.queryset
        user = getattr(self.request, 'user', None)
        if not user or not user.is_authenticated:
            return queryset.none()

        sales_item_id = self.request.query_params.get('sales_item_id')
        if sales_item_id:
            queryset = queryset.filter(sales_order_id=sales_item_id)

        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        approver_id = self.request.query_params.get('approver_id')
        if approver_id:
            queryset = queryset.filter(approved_by_id=approver_id)

        if not user.has_perm('document.can_view_all_sales'):
            queryset = queryset.filter(
                Q(sales_order__sales_order__cart__user=user) |
                Q(approved_by=user)
            )

        return queryset
