from django.urls import path

from . import views


urlpatterns = [
    path('create/', views.CreateCartView.as_view(), name='create_cart'),
    path('my/', views.UserCartListView.as_view(), name='user_carts'),
    path('<uuid:id>/', views.CartDetailView.as_view(), name='cart_detail'),
    path('<uuid:id>/update/', views.UpdateCartView.as_view(), name='update_cart'),
    path('<uuid:id>/delete/', views.DeleteCartView.as_view(), name='delete_cart'),
    path('<uuid:id>/checkout/', views.CheckoutCartView.as_view(), name='checkout_cart'),
    path('<uuid:cart_id>/items/add/', views.AddItemToCartView.as_view(), name='add_cart_item'),
    path('items/<uuid:id>/', views.RemoveCartItemView.as_view(), name='remove_cart_item'),
    path('items/<uuid:id>/update/', views.UpdateCartItemView.as_view(), name='update_cart_item'),
]
