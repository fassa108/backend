from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AssignationViewSet,
    BriefViewSet,
    FichierLivrableViewSet,
    LivrableViewSet,
    RessourceViewSet,
)

router = DefaultRouter()

router.register("ressources", RessourceViewSet, basename="ressource")
router.register("briefs", BriefViewSet, basename="brief")
router.register(
    "assignations",
    AssignationViewSet,
    basename="assignation",
)
router.register(
    "livrables",
    LivrableViewSet,
    basename="livrable",
)
router.register(
    "fichiers-livrables",
    FichierLivrableViewSet,
    basename="fichier-livrable",
)

urlpatterns = [
    path(
        "tenants/<int:tenant_id>/",
        include(router.urls),
    ),
]
