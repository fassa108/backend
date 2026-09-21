from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.models import MembreTenant
from tenants.models import Tenant
from .models import (
    Competence,
    CompetenceNiveau,
    Formation,
    Groupe,
    GroupeMembre,
    InscriptionPromotion,
    Module,
    Niveau,
    Promotion,
)
from .permissions import IsTenantAdminOrSaaSAdmin
from .serializers import (
    CompetenceNiveauSerializer,
    CompetenceSerializer,
    FormationSerializer,
    GroupeMembreSerializer,
    GroupeSerializer,
    InscriptionPromotionSerializer,
    ModuleSerializer,
    NiveauSerializer,
    PromotionSerializer,
)

Utilisateur = get_user_model()


class FormationViewSet(viewsets.ModelViewSet):
    serializer_class = FormationSerializer
    permission_classes = [IsTenantAdminOrSaaSAdmin]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        return Formation.objects.filter(tenant_id=tenant_id).order_by("nom")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs.get("tenant_id")
        return context

    def perform_create(self, serializer):
        tenant_id = self.kwargs.get("tenant_id")
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        serializer.save(tenant=tenant)


class PromotionViewSet(viewsets.ModelViewSet):
    serializer_class = PromotionSerializer
    permission_classes = [IsTenantAdminOrSaaSAdmin]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        qs = Promotion.objects.filter(
            formation__tenant_id=tenant_id
        ).select_related("formation").order_by("-date_debut")

        formation_id = self.request.query_params.get("formation")
        if formation_id:
            qs = qs.filter(formation_id=formation_id)
        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs.get("tenant_id")
        return context

    @action(detail=True, methods=["post"], url_path="inscrire-apprenant")
    def inscrire_apprenant(self, request, tenant_id=None, pk=None):
        promotion = self.get_object()
        apprenant_id = request.data.get("apprenant_id")
        if not apprenant_id:
            return Response(
                {"apprenant_id": "Ce champ est obligatoire."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        apprenant = get_object_or_404(Utilisateur, pk=apprenant_id)

        # 1. Utilisateur actif
        if not apprenant.actif:
            return Response(
                {"apprenant": "L'utilisateur sélectionné est inactif."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 2. Rôle APPRENANT actif dans le même tenant
        tenant = promotion.formation.tenant
        if not MembreTenant.objects.filter(
            utilisateur=apprenant,
            tenant=tenant,
            role=MembreTenant.Role.APPRENANT,
            actif=True,
        ).exists():
            return Response(
                {"apprenant": "L'utilisateur doit être un apprenant actif de cet organisme."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 3. Unicité d'une seule promotion active à la fois
        active_inscription = InscriptionPromotion.objects.filter(
            apprenant=apprenant,
            actif=True,
        ).select_related("promotion").first()

        if active_inscription:
            if active_inscription.promotion_id == promotion.id:
                return Response(
                    {"apprenant": "L'apprenant est déjà inscrit dans cette promotion."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            else:
                return Response(
                    {"apprenant": f"L'apprenant a déjà une inscription active dans la promotion '{active_inscription.promotion.nom}'."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # 4. Si une inscription inactive existe déjà pour cette promotion, on la réactive
        existing = InscriptionPromotion.objects.filter(
            promotion=promotion,
            apprenant=apprenant,
        ).first()

        if existing:
            existing.actif = True
            existing.date_desinscription = None
            existing.save()
            inscription = existing
            status_code = status.HTTP_200_OK
        else:
            inscription = InscriptionPromotion.objects.create(
                promotion=promotion,
                apprenant=apprenant,
                actif=True,
            )
            status_code = status.HTTP_201_CREATED

        serializer = InscriptionPromotionSerializer(inscription)
        return Response(serializer.data, status=status_code)

    @action(detail=True, methods=["post"], url_path="desinscrire-apprenant")
    def desinscrire_apprenant(self, request, tenant_id=None, pk=None):
        promotion = self.get_object()
        apprenant_id = request.data.get("apprenant_id")
        if not apprenant_id:
            return Response(
                {"apprenant_id": "Ce champ est obligatoire."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        inscription = InscriptionPromotion.objects.filter(
            promotion=promotion,
            apprenant_id=apprenant_id,
            actif=True,
        ).first()

        if not inscription:
            return Response(
                {"apprenant": "Aucune inscription active trouvée pour cet apprenant dans cette promotion."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        inscription.actif = False
        inscription.date_desinscription = timezone.now()
        inscription.save()

        return Response(
            {"detail": "Apprenant désinscrit avec succès."},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"], url_path="inscriptions")
    def inscriptions(self, request, tenant_id=None, pk=None):
        promotion = self.get_object()
        qs = promotion.inscriptions.select_related("apprenant").all()
        actif_param = request.query_params.get("actif")
        if actif_param is not None:
            qs = qs.filter(actif=actif_param.lower() in ["true", "1"])
        serializer = InscriptionPromotionSerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ModuleViewSet(viewsets.ModelViewSet):
    serializer_class = ModuleSerializer
    permission_classes = [IsTenantAdminOrSaaSAdmin]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        qs = Module.objects.filter(
            formation__tenant_id=tenant_id
        ).select_related("formation").order_by("ordre", "nom")

        formation_id = self.request.query_params.get("formation")
        if formation_id:
            qs = qs.filter(formation_id=formation_id)
        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs.get("tenant_id")
        return context


class CompetenceViewSet(viewsets.ModelViewSet):
    serializer_class = CompetenceSerializer
    permission_classes = [IsTenantAdminOrSaaSAdmin]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        qs = Competence.objects.filter(
            module__formation__tenant_id=tenant_id
        ).select_related("module", "module__formation").order_by("ordre", "nom")

        module_id = self.request.query_params.get("module")
        if module_id:
            qs = qs.filter(module_id=module_id)
        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs.get("tenant_id")
        return context


class NiveauViewSet(viewsets.ModelViewSet):
    serializer_class = NiveauSerializer
    permission_classes = [IsTenantAdminOrSaaSAdmin]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        return Niveau.objects.filter(tenant_id=tenant_id).order_by("ordre", "nom")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs.get("tenant_id")
        return context

    def perform_create(self, serializer):
        tenant_id = self.kwargs.get("tenant_id")
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        serializer.save(tenant=tenant)


class CompetenceNiveauViewSet(viewsets.ModelViewSet):
    serializer_class = CompetenceNiveauSerializer
    permission_classes = [IsTenantAdminOrSaaSAdmin]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        qs = CompetenceNiveau.objects.filter(
            competence__module__formation__tenant_id=tenant_id
        ).select_related(
            "competence",
            "competence__module",
            "competence__module__formation",
            "niveau",
        ).order_by("niveau__ordre", "competence__ordre")

        competence_id = self.request.query_params.get("competence")
        if competence_id:
            qs = qs.filter(competence_id=competence_id)
        niveau_id = self.request.query_params.get("niveau")
        if niveau_id:
            qs = qs.filter(niveau_id=niveau_id)
        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs.get("tenant_id")
        return context


class GroupeViewSet(viewsets.ModelViewSet):
    serializer_class = GroupeSerializer
    permission_classes = [IsTenantAdminOrSaaSAdmin]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        qs = Groupe.objects.filter(
            promotion__formation__tenant_id=tenant_id
        ).select_related(
            "promotion",
            "promotion__formation",
        ).prefetch_related(
            "membres",
            "membres__apprenant",
        ).order_by("nom")

        promotion_id = self.request.query_params.get("promotion")
        if promotion_id:
            qs = qs.filter(promotion_id=promotion_id)
        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs.get("tenant_id")
        return context

    @action(detail=True, methods=["post"], url_path="ajouter-apprenant")
    def ajouter_apprenant(self, request, tenant_id=None, pk=None):
        groupe = self.get_object()
        apprenant_id = request.data.get("apprenant_id")
        if not apprenant_id:
            return Response(
                {"apprenant_id": "Ce champ est obligatoire."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        apprenant = get_object_or_404(Utilisateur, pk=apprenant_id)

        # 1. Utilisateur actif
        if not apprenant.actif:
            return Response(
                {"apprenant": "L'utilisateur sélectionné est inactif."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 2. Rôle APPRENANT actif dans le même tenant
        tenant = groupe.promotion.formation.tenant
        if not MembreTenant.objects.filter(
            utilisateur=apprenant,
            tenant=tenant,
            role=MembreTenant.Role.APPRENANT,
            actif=True,
        ).exists():
            return Response(
                {"apprenant": "L'utilisateur doit être un apprenant actif de cet organisme."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 3. Inscription active dans LA PROMOTION DU GROUPE
        inscription = InscriptionPromotion.objects.filter(
            apprenant=apprenant,
            actif=True,
        ).select_related("promotion").first()

        if not inscription:
            return Response(
                {"apprenant": "L'apprenant n'a aucune inscription active dans une promotion."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if inscription.promotion_id != groupe.promotion_id:
            return Response(
                {"apprenant": f"L'apprenant appartient à la promotion '{inscription.promotion.nom}' et ne peut pas être ajouté à un groupe de la promotion '{groupe.promotion.nom}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 4. Déjà membre du groupe
        if GroupeMembre.objects.filter(groupe=groupe, apprenant=apprenant).exists():
            return Response(
                {"apprenant": "Cet apprenant est déjà membre de ce groupe."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        membre = GroupeMembre.objects.create(groupe=groupe, apprenant=apprenant)
        serializer = GroupeMembreSerializer(membre)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="retirer-apprenant")
    def retirer_apprenant(self, request, tenant_id=None, pk=None):
        groupe = self.get_object()
        apprenant_id = request.data.get("apprenant_id")
        if not apprenant_id:
            return Response(
                {"apprenant_id": "Ce champ est obligatoire."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        membre = GroupeMembre.objects.filter(groupe=groupe, apprenant_id=apprenant_id).first()
        if not membre:
            return Response(
                {"apprenant": "Cet apprenant n'est pas membre de ce groupe."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        membre.delete()
        return Response(
            {"detail": "Apprenant retiré du groupe avec succès."},
            status=status.HTTP_200_OK,
        )

