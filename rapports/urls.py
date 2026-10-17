from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ExportMembresView, ExportProgressionView, ExportSuiviView, RapportViewSet

router = DefaultRouter()
router.register("rapports", RapportViewSet, basename="rapport")

urlpatterns = [
    path(
        "tenants/<int:tenant_id>/exports/promotions/<int:promotion_id>/progression/",
        ExportProgressionView.as_view(),
        name="export-progression",
    ),
    path(
        "tenants/<int:tenant_id>/exports/promotions/<int:promotion_id>/suivi/",
        ExportSuiviView.as_view(),
        name="export-suivi",
    ),
    path(
        "tenants/<int:tenant_id>/exports/membres/",
        ExportMembresView.as_view(),
        name="export-membres",
    ),
    path(
        "tenants/<int:tenant_id>/",
        include(router.urls),
    ),
]
