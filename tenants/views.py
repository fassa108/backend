from django.conf import settings
from django.http import HttpResponse
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle

from accounts.models import MembreTenant, Utilisateur
from pedagogie.models import Formation, Promotion

from .abonnements import abonnement_de, lever_suspension_par_admin_saas, resume as resume_abonnement
from .models import DemandeInscription, Paiement, Tenant
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
from . import finances, paydunya
from .services import AbonnementService, DemandeInscriptionService, TenantService
from .tasks import envoyer_email_lien_paiement


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
        # Réactivation par l'admin SaaS d'un organisme suspendu pour impayé
        if serializer.validated_data.get("statut") is True:
            lever_suspension_par_admin_saas(tenant)

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

    - Public : dépôt du formulaire, puis paiement via PayDunya, depuis la
      page accessible par la référence secrète renvoyée au dépôt :
      POST paiement/<ref>/ crée la facture et renvoie l'adresse de la page
      PayDunya ; au retour, POST paiement/<ref>/confirmer/ {token} relit la
      facture chez PayDunya et, si elle est payée, crée l'organisme.
    - PayDunya : callback paydunya/ipn/ (en production), même confirmation.
    - Admin SaaS : historique des inscriptions (filtrable par statut).
    """

    ACTIONS_PUBLIQUES = ("create", "paiement", "confirmer_paiement", "ipn")

    def get_queryset(self):
        queryset = DemandeInscription.objects.prefetch_related("paiements")
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
        if self.action == "confirmer_paiement":
            self.throttle_scope = "confirmation_paiement"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def get_serializer_class(self):
        if self.action == "create":
            return DemandeInscriptionCreationSerializer
        return DemandeInscriptionSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        demande = serializer.save()
        # Le lien de la page de paiement part aussi par email : le responsable
        # peut y revenir (payer plus tard, vérifier un paiement interrompu).
        transaction.on_commit(lambda: envoyer_email_lien_paiement.delay(
            demande.email, demande.responsable_prenom, demande.nom_organisme,
            str(demande.reference), settings.PRIX_ABONNEMENT_FCFA,
        ))
        # Seule la référence est renvoyée : elle ouvre la page de paiement.
        return Response(
            {"reference": demande.reference, "montant": settings.PRIX_ABONNEMENT_FCFA},
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(methods=["GET"], responses=InscriptionAPayerSerializer)
    @extend_schema(methods=["POST"], request=None, responses={201: dict})
    @action(
        detail=False,
        methods=["get", "post"],
        url_path=r"paiement/(?P<reference>[0-9a-f-]{36})",
    )
    def paiement(self, request, reference=None):
        demande = get_object_or_404(DemandeInscription, reference=reference)

        if request.method == "GET":
            # Paiement interrompu (page PayDunya fermée) : relu chez PayDunya
            DemandeInscriptionService.reverifier(demande)
            demande = DemandeInscription.objects.prefetch_related("paiements").get(pk=demande.pk)
            return Response(InscriptionAPayerSerializer(demande).data)

        url = DemandeInscriptionService.initier_paiement(reference)
        return Response({"url": url}, status=status.HTTP_201_CREATED)

    @extend_schema(request=dict, responses=PaiementSerializer)
    @action(
        detail=False,
        methods=["post"],
        url_path=r"paiement/(?P<reference>[0-9a-f-]{36})/confirmer",
    )
    def confirmer_paiement(self, request, reference=None):
        jeton = str(request.data.get("token") or "").strip()
        if not jeton:
            raise ValidationError({"token": "Jeton de paiement manquant."})
        paiement = DemandeInscriptionService.confirmer_paiement(jeton, reference=reference)
        return Response(PaiementSerializer(paiement).data)

    @extend_schema(exclude=True)
    @action(detail=False, methods=["post"], url_path="paydunya/ipn")
    def ipn(self, request):
        """
        Callback de PayDunya (formulaire « data[…] ») : authentifié par le
        hash de la clé principale, puis la facture est relue chez PayDunya.
        """
        hash_recu = _champ_ipn(request.data, "hash")
        jeton = _champ_ipn(request.data, "invoice", "token")
        if not paydunya.hash_valide(hash_recu):
            return Response(status=status.HTTP_403_FORBIDDEN)
        if jeton:
            try:
                DemandeInscriptionService.confirmer_paiement(jeton)
            except NotFound:
                pass  # facture d'un autre site ou supprimée : rien à faire
        return Response(status=status.HTTP_200_OK)


def _champ_ipn(donnees, *cles):
    """Champ du callback PayDunya : formulaire « data[invoice][token] » ou JSON imbriqué."""
    if not hasattr(donnees, "get"):
        return None
    valeur = donnees.get("data[" + "][".join(cles) + "]")
    if valeur:
        return valeur
    valeur = donnees.get("data")
    for cle in cles:
        valeur = valeur.get(cle) if isinstance(valeur, dict) else None
    return valeur


# ─── Abonnement de l'organisme ────────────────────────────────────────────────
# Routes en « organisme_id » (et non « tenant_id ») : le middleware de
# suspension ne les bloque pas, l'administrateur peut renouveler un
# organisme suspendu pour impayé.

class _AbonnementBaseView(APIView):
    permission_classes = [IsAuthenticated]

    def organisme(self, request, organisme_id, ecriture=False):
        tenant = get_object_or_404(Tenant.objects.select_related("abonnement"), pk=organisme_id)
        abonnement_de(tenant)
        est_admin = MembreTenant.objects.filter(
            utilisateur=request.user, tenant=tenant, role=MembreTenant.Role.ADMINISTRATEUR, actif=True,
        ).exists()
        if not est_admin and (ecriture or not request.user.est_admin_saas):
            raise PermissionDenied("Réservé aux administrateurs de l'organisme.")
        return tenant

    def reponse(self, tenant):
        tenant = Tenant.objects.select_related("abonnement").get(pk=tenant.pk)
        paiements = Paiement.objects.filter(tenant=tenant, statut=Paiement.Statut.REUSSI)
        return Response({
            "organisme": tenant.nom,
            "abonnement": resume_abonnement(tenant.abonnement),
            "paiements": PaiementSerializer(paiements, many=True).data,
            "mode_test": settings.PAYDUNYA_MODE != "live",
        })


class AbonnementView(_AbonnementBaseView):
    """GET : échéance, statut et historique des paiements (avec reçus)."""

    def get(self, request, organisme_id):
        tenant = self.organisme(request, organisme_id)
        # Renouvellement payé mais page PayDunya fermée avant le retour : relu chez PayDunya
        AbonnementService.reverifier(tenant)
        return self.reponse(tenant)


class RenouvellementView(_AbonnementBaseView):
    """POST : facture PayDunya d'un mois ; renvoie l'adresse de la page de paiement."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "paiement"

    def post(self, request, organisme_id):
        tenant = self.organisme(request, organisme_id, ecriture=True)
        url = AbonnementService.initier_renouvellement(tenant, request.user)
        return Response({"url": url}, status=status.HTTP_201_CREATED)


class ConfirmationRenouvellementView(_AbonnementBaseView):
    """POST {token} : retour de PayDunya, facture relue chez PayDunya."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "confirmation_paiement"

    def post(self, request, organisme_id):
        tenant = self.organisme(request, organisme_id, ecriture=True)
        jeton = str(request.data.get("token") or "").strip()
        if not jeton:
            raise ValidationError({"token": "Jeton de paiement manquant."})
        paiement = DemandeInscriptionService.confirmer_paiement(jeton, tenant=tenant)
        reponse = self.reponse(tenant)
        reponse.data["paiement"] = PaiementSerializer(paiement).data
        return reponse


# ─── Finances de la plateforme (admin SaaS) ───────────────────────────────────

def _mode_finances(request):
    mode = request.query_params.get("mode") or ("live" if settings.PAYDUNYA_MODE == "live" else "test")
    if mode not in Paiement.Mode.values:
        raise ValidationError({"mode": "« test » ou « live »."})
    return mode


class FinancesView(APIView):
    """Encaissements, tentatives, abonnements à échéance, dernières transactions.
    Filtres : ?mode=test|live, ?statut=, ?type=."""

    permission_classes = [IsAdminSaaS]

    def get(self, request):
        p = request.query_params
        return Response(finances.tableau(_mode_finances(request), p.get("statut", ""), p.get("type", "")))


class FinancesExportView(APIView):
    """Toutes les transactions (mêmes filtres) et une synthèse, au format Excel."""

    permission_classes = [IsAdminSaaS]

    def get(self, request):
        p = request.query_params
        reponse = HttpResponse(
            finances.export_excel(_mode_finances(request), p.get("statut", ""), p.get("type", "")),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        reponse["Content-Disposition"] = f'attachment; filename="transactions-{timezone.localdate():%Y-%m-%d}.xlsx"'
        return reponse
