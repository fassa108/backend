from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    AbonnementView,
    ConfirmationRenouvellementView,
    DemandeInscriptionViewSet,
    FinancesExportView,
    FinancesView,
    RenouvellementView,
    TenantViewSet,
)


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
    # « organisme_id » et non « tenant_id » : accessibles même organisme suspendu
    path("abonnements/<int:organisme_id>/", AbonnementView.as_view(), name="abonnement"),
    path("abonnements/<int:organisme_id>/renouveler/", RenouvellementView.as_view(), name="abonnement-renouveler"),
    path(
        "abonnements/<int:organisme_id>/confirmer/",
        ConfirmationRenouvellementView.as_view(),
        name="abonnement-confirmer",
    ),
    path("finances/", FinancesView.as_view(), name="finances"),
    path("finances/export/", FinancesExportView.as_view(), name="finances-export"),
]
