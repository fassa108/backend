from rest_framework import status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from accounts.models import MembreTenant
from pedagogie.models import FormateurPromotion

from .models import (
    Assignation,
    Brief,
    FichierLivrable,
    Livrable,
    Ressource,
    RessourceBrief,
)
from .permissions import (
    CanConsultAssignation,
    CanConsultBrief,
    CanConsultLivrable,
    CanConsultRessource,
    CanConsultRessourceBrief,
    CanCreateLivrable,
    CanManageAssignation,
    CanManageBrief,
    CanManageRessource,
    CanManageRessourceBrief,
    CanUpdateLivrableStatut,
)
from .serializers import (
    AssignationSerializer,
    BriefSerializer,
    FichierLivrableSerializer,
    LivrableSerializer,
    LivrableStatutSerializer,
    RessourceBriefSerializer,
    RessourceSerializer,
)


def _promotions_du_formateur(user, tenant_id):
    return FormateurPromotion.objects.filter(
        formateur=user,
        promotion__formation__tenant_id=tenant_id,
    ).values_list("promotion_id", flat=True)


def _est_admin_organisme(user, tenant_id):
    return MembreTenant.objects.filter(
        utilisateur=user,
        tenant_id=tenant_id,
        role=MembreTenant.Role.ADMINISTRATEUR,
        actif=True,
    ).exists()


# ─── Ressource indépendante ───────────────────────────────────────────────────

class RessourceViewSet(viewsets.ModelViewSet):
    """
    Ressources indépendantes d'un Brief.
    Consultation : tout membre actif du tenant.
    Gestion (CUD) : Formateur ou Admin organisme.
    Admin SaaS exclu.
    """

    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ["list", "retrieve"]:
            return [CanConsultRessource()]
        return [CanManageRessource()]

    def get_queryset(self):
        tenant_id = self.kwargs["tenant_id"]
        return Ressource.objects.filter(tenant_id=tenant_id).select_related(
            "formateur"
        ).order_by("-date_creation")

    def get_serializer_class(self):
        return RessourceSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs["tenant_id"]
        return context

    def perform_create(self, serializer):
        from tenants.models import Tenant
        from django.shortcuts import get_object_or_404
        tenant_id = self.kwargs["tenant_id"]
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        serializer.save(tenant=tenant, formateur=self.request.user)


# ─── Brief ────────────────────────────────────────────────────────────────────

class BriefViewSet(viewsets.ModelViewSet):
    """
    Consultation : APPRENANT, FORMATEUR, ADMINISTRATEUR (filtrage par promotion).
    Gestion : FORMATEUR (ses promotions uniquement).
    Admin SaaS exclu.
    """

    serializer_class = BriefSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ["list", "retrieve"]:
            return [CanConsultBrief()]
        elif self.action in ["create", "partial_update", "destroy"]:
            return [CanManageBrief()]
        return []

    def get_queryset(self):
        tenant_id = self.kwargs["tenant_id"]
        user = self.request.user

        base_qs = (
            Brief.objects.filter(
                promotion__formation__tenant_id=tenant_id
            )
            .select_related("promotion", "promotion__formation")
            .prefetch_related("competences")
        )

        # Admin organisme : tous les briefs du tenant
        if _est_admin_organisme(user, tenant_id):
            return base_qs

        # Formateur : uniquement ses promotions affectées
        role = MembreTenant.objects.filter(
            utilisateur=user, tenant_id=tenant_id, actif=True
        ).values_list("role", flat=True).first()

        if role == MembreTenant.Role.FORMATEUR:
            ids = _promotions_du_formateur(user, tenant_id)
            return base_qs.filter(promotion_id__in=ids)

        # Apprenant : tous les briefs de ses promotions (visibilité publique)
        return base_qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs["tenant_id"]
        context["request"] = self.request
        return context

    def perform_update(self, serializer):
        if serializer.instance.assignations.filter(livrables__isnull=False).exists():
            raise PermissionDenied(
                "Ce brief ne peut pas être modifié car il possède des livrables."
            )
        serializer.save()

    def perform_destroy(self, instance):
        if instance.assignations.filter(livrables__isnull=False).exists():
            raise PermissionDenied(
                "Ce brief ne peut pas être supprimé car il possède des livrables."
            )
        instance.delete()


# ─── RessourceBrief (déprécié) ────────────────────────────────────────────────

class RessourceBriefViewSet(viewsets.ModelViewSet):
    """
    DÉPRÉCIÉ — conservé pour compatibilité.
    Utiliser RessourceViewSet à la place.
    """

    serializer_class = RessourceBriefSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ["list", "retrieve"]:
            return [CanConsultRessourceBrief()]
        elif self.action in ["create", "partial_update", "destroy"]:
            return [CanManageRessourceBrief()]
        return []

    def get_queryset(self):
        return RessourceBrief.objects.filter(
            brief__promotion__formation__tenant_id=self.kwargs["tenant_id"]
        ).select_related("brief", "brief__promotion", "brief__promotion__formation")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs["tenant_id"]
        context["request"] = self.request
        return context


# ─── Assignation ──────────────────────────────────────────────────────────────

class AssignationViewSet(viewsets.ModelViewSet):
    """
    Consultation : APPRENANT, FORMATEUR, ADMINISTRATEUR.
    Gestion : FORMATEUR (ses promotions uniquement — vérifié dans le serializer).
    Admin SaaS exclu.
    """

    serializer_class = AssignationSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ["list", "retrieve"]:
            return [CanConsultAssignation()]
        elif self.action in ["create", "partial_update", "destroy"]:
            return [CanManageAssignation()]
        return []

    def get_queryset(self):
        tenant_id = self.kwargs["tenant_id"]
        user = self.request.user

        base_qs = Assignation.objects.filter(
            brief__promotion__formation__tenant_id=tenant_id
        ).select_related(
            "brief",
            "brief__promotion",
            "brief__promotion__formation",
            "groupe",
            "apprenant",
        )

        if _est_admin_organisme(user, tenant_id):
            return base_qs

        role = MembreTenant.objects.filter(
            utilisateur=user, tenant_id=tenant_id, actif=True
        ).values_list("role", flat=True).first()

        if role == MembreTenant.Role.FORMATEUR:
            ids = _promotions_du_formateur(user, tenant_id)
            return base_qs.filter(brief__promotion_id__in=ids)

        return base_qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs["tenant_id"]
        context["request"] = self.request
        return context


# ─── Livrable ─────────────────────────────────────────────────────────────────

class LivrableViewSet(viewsets.ModelViewSet):
    """
    Consultation : tout membre actif (filtrage par promotion pour formateur).
    Création : APPRENANT uniquement.
    PATCH statut : FORMATEUR (ses promotions uniquement).
    Admin SaaS exclu.
    """

    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_permissions(self):
        if self.action in ["list", "retrieve"]:
            return [CanConsultLivrable()]
        elif self.action == "create":
            return [CanCreateLivrable()]
        elif self.action == "partial_update":
            return [CanUpdateLivrableStatut()]
        return []

    def get_queryset(self):
        tenant_id = self.kwargs["tenant_id"]
        user = self.request.user

        base_qs = (
            Livrable.objects.filter(
                assignation__brief__promotion__formation__tenant_id=tenant_id
            )
            .select_related(
                "assignation",
                "assignation__brief",
                "assignation__brief__promotion",
                "assignation__brief__promotion__formation",
                "assignation__groupe",
                "assignation__apprenant",
                "deposant",
            )
            .prefetch_related("fichiers")
        )

        if _est_admin_organisme(user, tenant_id):
            return base_qs

        role = MembreTenant.objects.filter(
            utilisateur=user, tenant_id=tenant_id, actif=True
        ).values_list("role", flat=True).first()

        if role == MembreTenant.Role.FORMATEUR:
            ids = _promotions_du_formateur(user, tenant_id)
            return base_qs.filter(assignation__brief__promotion_id__in=ids)

        return base_qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs["tenant_id"]
        context["request"] = self.request
        return context

    def get_serializer_class(self):
        if self.action == "partial_update":
            return LivrableStatutSerializer
        return LivrableSerializer

    def perform_create(self, serializer):
        serializer.save(deposant=self.request.user)

    def partial_update(self, request, *args, **kwargs):
        livrable = self.get_object()
        serializer = self.get_serializer(livrable, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data, status=status.HTTP_200_OK)


# ─── FichierLivrable ──────────────────────────────────────────────────────────

class FichierLivrableViewSet(viewsets.ModelViewSet):
    serializer_class = FichierLivrableSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        if self.action in ["list", "retrieve"]:
            return [CanConsultLivrable()]
        elif self.action == "create":
            return [CanCreateLivrable()]
        return []

    def get_queryset(self):
        return FichierLivrable.objects.filter(
            livrable__assignation__brief__promotion__formation__tenant_id=(
                self.kwargs["tenant_id"]
            )
        ).select_related(
            "livrable",
            "livrable__assignation",
            "livrable__assignation__brief",
            "livrable__assignation__brief__promotion",
            "livrable__assignation__brief__promotion__formation",
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs["tenant_id"]
        context["request"] = self.request
        return context
