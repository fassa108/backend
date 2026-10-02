from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import SupportRevisionViewSet

router = DefaultRouter()
router.register("supports-revision", SupportRevisionViewSet, basename="support-revision")

urlpatterns = [
    path(
        "tenants/<int:tenant_id>/",
        include(router.urls),
    ),
]
