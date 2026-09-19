from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    CompetenceNiveauViewSet,
    CompetenceViewSet,
    FormationViewSet,
    ModuleViewSet,
    NiveauViewSet,
    PromotionViewSet,
)

router = DefaultRouter()
router.register("formations", FormationViewSet, basename="formation")
router.register("promotions", PromotionViewSet, basename="promotion")
router.register("modules", ModuleViewSet, basename="module")
router.register("competences", CompetenceViewSet, basename="competence")
router.register("niveaux", NiveauViewSet, basename="niveau")
router.register("competence-niveaux", CompetenceNiveauViewSet, basename="competence-niveau")

urlpatterns = [
    path(
        "tenants/<int:tenant_id>/",
        include(router.urls),
    ),
]
