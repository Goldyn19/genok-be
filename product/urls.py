from . import views
from django.urls import path

urlpatterns = [
    # location
    path('location/create/', views.LocationCreateView.as_view(), name='create_location'),
    path('location/import-csv/', views.LocationImportCSVView.as_view(), name='import_locations_csv'),
    path('location/', views.LocationListView.as_view(), name='list_location'),
    path('location/<str:pk>/', views.LocationRetrieveUpdateDestroyView.as_view(), name='location_detail'),
    path('stock/create/', views.StockCreateView.as_view(), name='create_stock'),
    path('stock/', views.StockListView.as_view(), name='list_stock'),
    path('stock/<str:pk>/', views.StockRetrieveUpdateDestroyView.as_view(), name='stock_detail'),
]
