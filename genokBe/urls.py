"""
URL configuration for genokBe project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from rest_framework import permissions
from drf_yasg.views import get_schema_view
from drf_yasg import openapi

schema_view = get_schema_view(
   openapi.Info(
      title="Genok API",
      default_version='v1',
       description="""
      # Purchase Approval System API

      This API manages purchase requests with a 5-step approval workflow.

      ## Approval Flow
      1. Staff creates purchase → 5 approval steps auto-created
      2. Step 1: First approval (permission: `can_do_first_approval`)
      3. Step 2: Second approval (permission: `can_do_second_approval`)
      4. Step 3: Third approval (permission: `can_do_third_approval`)
      5. Step 4: Fourth approval (permission: `can_do_fourth_approval`)
      6. Step 5: Final approval (permission: `can_do_final_approval`)
      7. After final approval → Stock automatically updated

      ## Permissions
      - `can_do_first_approval` through `can_do_final_approval`: Step-specific approval permissions
      - `can_reject_purchase`: Can reject purchases
      - `can_view_all_purchases`: Can view all purchases (not just own)
      - `can_create_purchase`: Can create purchases
      - `can_cancel_purchase`: Can cancel purchases

      ## Status Values
      - `pending`: Awaiting approvals
      - `approved`: All approvals done, waiting for stock update
      - `confirmed`: Stock updated successfully
      - `failed`: Purchase rejected
      """,
      terms_of_service="https://www.google.com/policies/terms/",
      contact=openapi.Contact(email="contact@snippets.local"),
      license=openapi.License(name="BSD License"),
   ),
   public=True,
   permission_classes=[permissions.AllowAny, ],
)

urlpatterns = [
    path('swagger<format>/', schema_view.without_ui(cache_timeout=0), name='schema-json'),
    path('', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    path('admin/', admin.site.urls),
    path('auth/', include('members.urls')),
    path('product/', include('product.urls')),
    path('cart/', include('cart.urls')),
    path('roles/', include('roles.urls')),
    path('purchases/', include('document.urls')),
]
