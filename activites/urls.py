from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AssignationViewSet,
    BriefViewSet,
    RessourceBriefViewSet,
)

router = DefaultRouter()

router.register("briefs", BriefViewSet, basename="brief")
router.register(
    "ressources-briefs",
    RessourceBriefViewSet,
    basename="ressource-brief",
)
router.register(
    "assignations",
    AssignationViewSet,
    basename="assignation",
)

urlpatterns = [
    path(
        "tenants/<int:tenant_id>/",
        include(router.urls),
    ),
]