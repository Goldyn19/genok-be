from .serializers import CartItemSerializer, CartSerializer
from rest_framework import mixins, generics, status
from rest_framework.permissions import IsAuthenticated
from .models import Cart, CartItem
from rest_framework.request import Request
from django.shortcuts import get_object_or_404
from drf_yasg.utils import swagger_auto_schema
from rest_framework.response import Response
from document.serializers import SalesOrderCreateSerializer


class CreateCartView(mixins.CreateModelMixin, generics.GenericAPIView):
    queryset = Cart.objects.all()
    serializer_class = CartSerializer
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Create cart',
        operation_description='Create a cart for the current authenticated user.',
        request_body=CartSerializer,
        responses={201: CartSerializer},
        tags=['Cart'],
    )
    def post(self, request: Request, *args, **kwargs):
        return self.create(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class UserCartListView(mixins.ListModelMixin, generics.GenericAPIView):
    serializer_class = CartSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Cart.objects.filter(user=self.request.user).order_by("-created_at")

    @swagger_auto_schema(
        operation_summary='List my carts',
        operation_description='List carts owned by the current authenticated user.',
        responses={200: CartSerializer(many=True)},
        tags=['Cart'],
    )
    def get(self, request: Request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class CartDetailView(mixins.RetrieveModelMixin, generics.GenericAPIView):
    serializer_class = CartSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "id"

    def get_queryset(self):
        return Cart.objects.filter(user=self.request.user)

    @swagger_auto_schema(
        operation_summary='Get cart',
        operation_description='Retrieve a single cart owned by the current user.',
        responses={200: CartSerializer},
        tags=['Cart'],
    )
    def get(self, request: Request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)


class UpdateCartView(mixins.UpdateModelMixin, generics.GenericAPIView):
    serializer_class = CartSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "id"

    def get_queryset(self):
        return Cart.objects.filter(user=self.request.user)

    @swagger_auto_schema(
        operation_summary='Update cart',
        operation_description='Update a cart owned by the current user. Only allowed while not checked out.',
        request_body=CartSerializer,
        responses={200: CartSerializer},
        tags=['Cart'],
    )
    def patch(self, request: Request, *args, **kwargs):
        cart = self.get_object()
        if cart.is_checked_out:
            return Response({'detail': 'Cart is checked out and cannot be edited.'}, status=status.HTTP_400_BAD_REQUEST)
        return self.partial_update(request, *args, **kwargs)


class DeleteCartView(mixins.DestroyModelMixin, generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    lookup_field = "id"

    def get_queryset(self):
        return Cart.objects.filter(user=self.request.user)

    @swagger_auto_schema(
        operation_summary='Delete cart',
        operation_description='Delete an open cart owned by the current user.',
        responses={204: 'No Content'},
        tags=['Cart'],
    )
    def delete(self, request: Request, *args, **kwargs):
        cart = self.get_object()
        if cart.is_checked_out:
            return Response({'detail': 'Checked out carts cannot be deleted.'}, status=status.HTTP_400_BAD_REQUEST)
        CartItem.objects.filter(cart=cart).delete()
        return self.destroy(request, *args, **kwargs)


class AddItemToCartView(mixins.CreateModelMixin, generics.GenericAPIView):
    queryset = CartItem.objects.all()
    serializer_class = CartItemSerializer
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='Add item to cart',
        operation_description='Add a product to a cart. If it already exists, quantity is incremented.',
        request_body=CartItemSerializer,
        responses={201: CartItemSerializer},
        tags=['Cart'],
    )
    def post(self, request: Request, *args, **kwargs):
        cart_id = self.kwargs["cart_id"]
        cart = get_object_or_404(Cart, id=cart_id, user=self.request.user)
        if cart.is_checked_out:
            return Response({'detail': 'Cart is checked out and cannot be edited.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product = serializer.validated_data["product"]
        quantity = serializer.validated_data.get("quantity", 1)
        unit_price = serializer.validated_data.get("unit_price", None)

        item = CartItem.objects.filter(cart=cart, product=product).first()
        if item:
            item.quantity += int(quantity)
            if unit_price is not None:
                item.unit_price = unit_price
            item.save()
            return Response(CartItemSerializer(item).data, status=status.HTTP_200_OK)

        item = serializer.save(user=self.request.user, cart=cart)
        return Response(CartItemSerializer(item).data, status=status.HTTP_201_CREATED)


class UpdateCartItemView(mixins.UpdateModelMixin, generics.GenericAPIView):
    serializer_class = CartItemSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "id"

    def get_queryset(self):
        return CartItem.objects.filter(user=self.request.user)

    @swagger_auto_schema(
        operation_summary='Update cart item',
        operation_description='Update quantity/unit_price for a cart item owned by the current user (only if cart is open).',
        request_body=CartItemSerializer,
        responses={200: CartItemSerializer},
        tags=['Cart'],
    )
    def patch(self, request: Request, *args, **kwargs):
        item = self.get_object()
        if item.cart.is_checked_out:
            return Response({'detail': 'Cart is checked out and cannot be edited.'}, status=status.HTTP_400_BAD_REQUEST)
        return self.partial_update(request, *args, **kwargs)


class CheckoutCartView(generics.GenericAPIView):
    serializer_class = SalesOrderCreateSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "id"

    def get_queryset(self):
        return Cart.objects.filter(user=self.request.user)

    def post(self, request, *args, **kwargs):
        cart = self.get_object()

        serializer = self.get_serializer(
            data=request.data,
            context={'request': request, 'cart': cart}
        )
        serializer.is_valid(raise_exception=True)
        sales_order = serializer.save()

        return Response({
            "sales_order_id": str(sales_order.id),
            "total": sales_order.total_amount,
            "payment_method": sales_order.payment_method
        }, status=200)
class RemoveCartItemView(mixins.DestroyModelMixin, generics.GenericAPIView):
    queryset = CartItem.objects.all()
    permission_classes = [IsAuthenticated]
    lookup_field = "id"

    @swagger_auto_schema(
        operation_summary='Remove cart item',
        operation_description='Remove an item from the current user\'s carts.',
        responses={204: 'No Content'},
        tags=['Cart'],
    )
    def delete(self, request: Request, *args, **kwargs):
        return self.destroy(request, *args, **kwargs)

    def get_queryset(self):
        return CartItem.objects.filter(user=self.request.user)
