from django.db.models import Count, Prefetch, Q
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from accounts.models import MembreTenant, Utilisateur
from pedagogie.models import Formation, Promotion

from .models import Tenant
from .permissions import IsAdminSaaS, IsAdminOrganisme
from .serializers import (
    IndicateursGlobauxSerializer,
    TenantCreationSerializer,
    TenantDetailSerializer,
    TenantSerializer,
    TenantStatutSerializer,
)
from .services import TenantService


def _compte_membres(role):
    return Count(
        "membres",
        filter=Q(membres__role=role, membres__actif=True),
        distinct=True,
    )


class TenantViewSet(viewsets.ModelViewSet):
    """
    Gestion des organismes.

    - Admin SaaS : liste, fiche (informations, indicateurs, admins),
      création avec le premier admin, suspension / réactivation,
      suppression d'un organisme vide.
    - Admin d'organisme : fiche et modification des informations
      de son organisme (hors statut).
    """

    # On garde GET, POST, PATCH et DELETE.
    # PUT n'est pas exposé.
    http_method_names = ["get", "post", "patch", "delete"]

    def get_queryset(self):
        return (
            Tenant.objects
            .annotate(
                nb_administrateurs=_compte_membres(MembreTenant.Role.ADMINISTRATEUR),
                nb_formateurs=_compte_membres(MembreTenant.Role.FORMATEUR),
                nb_apprenants=_compte_membres(MembreTenant.Role.APPRENANT),
                nb_formations=Count("formations", distinct=True),
                nb_promotions=Count("formations__promotions", distinct=True),
            )
            .prefetch_related(
                Prefetch(
                    "membres",
                    queryset=MembreTenant.objects.select_related("utilisateur"),
                )
            )
            .order_by("-date_creation")
        )

    def get_permissions(self):
        if self.action in ("retrieve", "partial_update"):
            permission_classes = [IsAdminSaaS | IsAdminOrganisme]
        else:
            # list, create, destroy, indicateurs : Admin SaaS uniquement.
            permission_classes = [IsAdminSaaS]

        return [permission() for permission in permission_classes]

    def get_serializer_class(self):
        if self.action == "create":
            return TenantCreationSerializer

        if self.action == "partial_update":
            # L'admin SaaS ne modifie que le statut ;
            # l'admin d'organisme tout sauf le statut.
            if self.request.user.est_admin_saas:
                return TenantStatutSerializer
            return TenantSerializer

        return TenantDetailSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        tenant = TenantService.creer_organisme(**serializer.validated_data)

        return Response(
            TenantDetailSerializer(self.get_queryset().get(pk=tenant.pk)).data,
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, *args, **kwargs):
        tenant = self.get_object()

        serializer = self.get_serializer(
            tenant,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            TenantDetailSerializer(self.get_queryset().get(pk=tenant.pk)).data
        )

    def perform_destroy(self, instance):
        if not TenantService.est_vide(instance):
            raise PermissionDenied(
                "Cet organisme ne peut pas être supprimé car il contient "
                "des données ou des membres. Suspendez-le à la place."
            )
        instance.delete()

    @extend_schema(responses=IndicateursGlobauxSerializer)
    @action(detail=False, methods=["get"])
    def indicateurs(self, request):
        """
        Indicateurs globaux de la plateforme (Admin SaaS).
        """

        organismes = Tenant.objects.aggregate(
            nb_organismes=Count("id"),
            nb_organismes_actifs=Count("id", filter=Q(statut=True)),
            nb_organismes_suspendus=Count("id", filter=Q(statut=False)),
        )

        data = {
            **organismes,
            "nb_utilisateurs": Utilisateur.objects.filter(
                est_admin_saas=False
            ).count(),
            "nb_formations": Formation.objects.count(),
            "nb_promotions": Promotion.objects.count(),
        }

        return Response(IndicateursGlobauxSerializer(data).data)
