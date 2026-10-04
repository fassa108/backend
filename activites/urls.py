from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AssignationViewSet,
    BriefViewSet,
    CategorieBriefViewSet,
    CommentairePairViewSet,
    EvaluationViewSet,
    FichierLivrableViewSet,
    LimitesFichiersView,
    LivrableViewSet,
    ProgressionViewSet,
    RessourceViewSet,
)

router = DefaultRouter()

router.register("ressources", RessourceViewSet, basename="ressource")
router.register("briefs", BriefViewSet, basename="brief")
router.register("categories-briefs", CategorieBriefViewSet, basename="categorie-brief")
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

router.register("evaluations", EvaluationViewSet, basename="evaluation")
router.register("progression", ProgressionViewSet, basename="progression")
router.register("commentaires", CommentairePairViewSet, basename="commentaire")

urlpatterns = [
    path(
        "limites-fichiers/",
        LimitesFichiersView.as_view(),
        name="limites-fichiers",
    ),
    path(
        "tenants/<int:tenant_id>/",
        include(router.urls),
    ),
]
