from django.conf import settings
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from accounts.models import MembreTenant, Utilisateur
from pedagogie.models import Formation, Promotion

from .models import DemandeInscription, Tenant
from .permissions import IsAdminSaaS, IsAdminOrganisme
from .serializers import (
    DemandeInscriptionCreationSerializer,
    DemandeInscriptionSerializer,
    IndicateursGlobauxSerializer,
    InscriptionAPayerSerializer,
    PaiementSerializer,
    TenantCreationSerializer,
    TenantDetailSerializer,
    TenantSerializer,
    TenantStatutSerializer,
)
from .services import DemandeInscriptionService, TenantService


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


class DemandeInscriptionViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    Inscriptions d'organismes.

    - Public : dépôt du formulaire, puis page de paiement (simulé),
      accessible par la référence secrète renvoyée au dépôt.
    - Admin SaaS : historique des inscriptions (filtrable par statut).
    """

    ACTIONS_PUBLIQUES = ("create", "paiement")

    def get_queryset(self):
        queryset = DemandeInscription.objects.select_related("paiement")
        statut = self.request.query_params.get("statut")
        if statut:
            queryset = queryset.filter(statut=statut)
        return queryset

    def get_permissions(self):
        if self.action in self.ACTIONS_PUBLIQUES:
            return [AllowAny()]
        return [IsAdminSaaS()]

    def get_throttles(self):
        # Débit limité sur les écritures publiques (dépôt et paiement)
        if self.action == "create":
            self.throttle_scope = "demande_inscription"
            return [ScopedRateThrottle()]
        if self.action == "paiement" and self.request.method == "POST":
            self.throttle_scope = "paiement"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def get_serializer_class(self):
        if self.action == "create":
            return DemandeInscriptionCreationSerializer
        if self.action == "paiement":
            return PaiementSerializer
        return DemandeInscriptionSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        demande = serializer.save()
        # Seule la référence est renvoyée : elle ouvre la page de paiement.
        return Response(
            {"reference": demande.reference, "montant": settings.PRIX_ABONNEMENT_FCFA},
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(methods=["GET"], responses=InscriptionAPayerSerializer)
    @extend_schema(methods=["POST"], request=PaiementSerializer, responses=PaiementSerializer)
    @action(
        detail=False,
        methods=["get", "post"],
        url_path=r"paiement/(?P<reference>[0-9a-f-]{36})",
    )
    def paiement(self, request, reference=None):
        demande = get_object_or_404(DemandeInscription, reference=reference)

        if request.method == "GET":
            return Response(InscriptionAPayerSerializer(demande).data)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        demande = DemandeInscriptionService.payer(reference, **serializer.validated_data)
        return Response(PaiementSerializer(demande.paiement).data, status=status.HTTP_201_CREATED)
