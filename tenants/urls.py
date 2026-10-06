from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import DemandeInscriptionViewSet, TenantViewSet


router = DefaultRouter()

router.register(
    "tenants",
    TenantViewSet,
    basename="tenant"
)
router.register(
    "demandes-inscription",
    DemandeInscriptionViewSet,
    basename="demande-inscription",
)

urlpatterns = [
    path("", include(router.urls)),
]