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
    FormateurPromotion,
    Formation,
    Groupe,
    GroupeMembre,
    InscriptionPromotion,
    Module,
    Niveau,
    Promotion,
)
from .permissions import (
    IsAdminOrganisme,
    IsAdminOrganismeOrFormateur,
    IsFormateurDePromotion,
    IsTenantAdminOrSaaSAdminOrAssignedLearner,
    _est_admin_organisme,
    _est_formateur_de_promotion,
)
from .serializers import (
    CompetenceNiveauSerializer,
    CompetenceSerializer,
    FormateurPromotionSerializer,
    FormationSerializer,
    GroupeMembreSerializer,
    GroupeSerializer,
    InscriptionPromotionSerializer,
    ModuleSerializer,
    NiveauSerializer,
    PromotionSerializer,
)

Utilisateur = get_user_model()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _promotions_du_formateur(user, tenant_id):
    """Retourne les IDs des promotions auxquelles le formateur est affecté."""
    return FormateurPromotion.objects.filter(
        formateur=user,
        promotion__formation__tenant_id=tenant_id,
    ).values_list("promotion_id", flat=True)


# ─── Formations ───────────────────────────────────────────────────────────────

class FormationViewSet(viewsets.ModelViewSet):
    """
    Admin organisme : CRUD complet sur les formations de son tenant.
    Admin SaaS : aucun accès (opération métier organisme).
    Formateur / Apprenant : aucun accès.
    """

    serializer_class = FormationSerializer
    permission_classes = [IsAdminOrganisme]
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


# ─── Promotions ───────────────────────────────────────────────────────────────

class PromotionViewSet(viewsets.ModelViewSet):
    """
    Admin organisme : CRUD complet sur ses promotions.
    Formateur : lecture seule sur ses promotions affectées.
    Admin SaaS : aucun accès (opération métier organisme).
    Apprenant : aucun accès.
    """

    serializer_class = PromotionSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ["list", "retrieve", "inscriptions",
                           "inscrire_apprenant", "desinscrire_apprenant"]:
            # Lecture : Admin organisme + Formateur (filtrage dans le QS)
            return [IsAdminOrganismeOrFormateur()]
        # Écriture : Admin organisme uniquement
        return [IsAdminOrganisme()]

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        user = self.request.user

        base_qs = Promotion.objects.filter(
            formation__tenant_id=tenant_id
        ).select_related("formation").order_by("-date_debut")

        # Filtre optionnel par formation
        formation_id = self.request.query_params.get("formation")
        if formation_id:
            base_qs = base_qs.filter(formation_id=formation_id)

        # Admin organisme : toutes les promotions du tenant
        if _est_admin_organisme(user, tenant_id):
            return base_qs

        # Formateur : uniquement ses promotions affectées
        ids = _promotions_du_formateur(user, tenant_id)
        return base_qs.filter(pk__in=ids)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs.get("tenant_id")
        return context

    @action(detail=True, methods=["post"], url_path="inscrire-apprenant")
    def inscrire_apprenant(self, request, tenant_id=None, pk=None):
        # Seul l'Admin organisme peut inscrire
        if not _est_admin_organisme(request.user, tenant_id):
            return Response(
                {"detail": "Seul l'administrateur de l'organisme peut inscrire un apprenant."},
                status=status.HTTP_403_FORBIDDEN,
            )

        promotion = self.get_object()
        apprenant_id = request.data.get("apprenant_id")
        if not apprenant_id:
            return Response(
                {"apprenant_id": "Ce champ est obligatoire."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        apprenant = get_object_or_404(Utilisateur, pk=apprenant_id)

        # Rôle APPRENANT actif dans le même tenant (Utilisateur.actif non vérifié)
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

        # Unicité d'une seule promotion active à la fois
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
                    {
                        "apprenant": (
                            f"L'apprenant a déjà une inscription active dans la promotion "
                            f"'{active_inscription.promotion.nom}'."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Réactivation si inscription inactive existante, sinon création
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
        if not _est_admin_organisme(request.user, tenant_id):
            return Response(
                {"detail": "Seul l'administrateur de l'organisme peut désinscrire un apprenant."},
                status=status.HTTP_403_FORBIDDEN,
            )

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

    @action(
        detail=True,
        methods=["post"],
        url_path="affecter-formateur",
    )
    def affecter_formateur(self, request, tenant_id=None, pk=None):
        """Affecter un formateur à une promotion. Admin organisme uniquement."""
        if not _est_admin_organisme(request.user, tenant_id):
            return Response(
                {"detail": "Seul l'administrateur de l'organisme peut affecter un formateur."},
                status=status.HTTP_403_FORBIDDEN,
            )

        promotion = self.get_object()
        serializer = FormateurPromotionSerializer(
            data=request.data,
            context={"tenant_id": int(tenant_id), "request": request},
        )

        # Injecter la promotion depuis l'URL si non fournie dans le body
        if "promotion" not in request.data:
            data = request.data.copy()
            data["promotion"] = promotion.id
            serializer = FormateurPromotionSerializer(
                data=data,
                context={"tenant_id": int(tenant_id), "request": request},
            )

        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["post"],
        url_path="retirer-formateur",
    )
    def retirer_formateur(self, request, tenant_id=None, pk=None):
        """Retirer un formateur d'une promotion. Admin organisme uniquement."""
        if not _est_admin_organisme(request.user, tenant_id):
            return Response(
                {"detail": "Seul l'administrateur de l'organisme peut retirer un formateur."},
                status=status.HTTP_403_FORBIDDEN,
            )

        promotion = self.get_object()
        formateur_id = request.data.get("formateur_id")
        if not formateur_id:
            return Response(
                {"formateur_id": "Ce champ est obligatoire."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        affectation = FormateurPromotion.objects.filter(
            promotion=promotion,
            formateur_id=formateur_id,
        ).first()

        if not affectation:
            return Response(
                {"detail": "Ce formateur n'est pas affecté à cette promotion."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        affectation.delete()
        return Response(
            {"detail": "Formateur retiré de la promotion avec succès."},
            status=status.HTTP_200_OK,
        )

    @action(
        detail=True,
        methods=["get"],
        url_path="formateurs",
    )
    def formateurs(self, request, tenant_id=None, pk=None):
        """Liste des formateurs affectés à une promotion."""
        promotion = self.get_object()
        affectations = FormateurPromotion.objects.filter(
            promotion=promotion,
        ).select_related("formateur")
        serializer = FormateurPromotionSerializer(affectations, many=True)
        return Response(serializer.data)


# ─── Modules ──────────────────────────────────────────────────────────────────

class ModuleViewSet(viewsets.ModelViewSet):
    serializer_class = ModuleSerializer
    permission_classes = [IsAdminOrganisme]
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


# ─── Compétences ──────────────────────────────────────────────────────────────

class CompetenceViewSet(viewsets.ModelViewSet):
    serializer_class = CompetenceSerializer
    permission_classes = [IsAdminOrganisme]
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


# ─── Niveaux ──────────────────────────────────────────────────────────────────

class NiveauViewSet(viewsets.ModelViewSet):
    serializer_class = NiveauSerializer
    permission_classes = [IsAdminOrganisme]
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


# ─── Compétence-Niveaux ───────────────────────────────────────────────────────

class CompetenceNiveauViewSet(viewsets.ModelViewSet):
    serializer_class = CompetenceNiveauSerializer
    permission_classes = [IsAdminOrganisme]
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


# ─── Groupes ──────────────────────────────────────────────────────────────────

class GroupeViewSet(viewsets.ModelViewSet):
    """
    Admin organisme : CRUD complet sur tous les groupes du tenant.
    Formateur : CRUD sur les groupes de ses promotions affectées.
    Admin SaaS : aucun accès.
    Apprenant : aucun accès.
    """

    serializer_class = GroupeSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        return [IsAdminOrganismeOrFormateur()]

    def _verifier_acces_groupe(self, groupe):
        """
        Vérifie que le formateur a accès au groupe (via sa promotion affectée).
        Retourne True si accès autorisé, False sinon.
        Un admin organisme a toujours accès.
        """
        user = self.request.user
        tenant_id = self.kwargs.get("tenant_id")

        if _est_admin_organisme(user, tenant_id):
            return True

        # Formateur : doit être affecté à la promotion du groupe
        return _est_formateur_de_promotion(user, groupe.promotion_id)

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        user = self.request.user

        base_qs = Groupe.objects.filter(
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
            base_qs = base_qs.filter(promotion_id=promotion_id)

        # Admin organisme : tous les groupes du tenant
        if _est_admin_organisme(user, tenant_id):
            return base_qs

        # Formateur : groupes de ses promotions affectées
        ids = _promotions_du_formateur(user, tenant_id)
        return base_qs.filter(promotion_id__in=ids)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs.get("tenant_id")
        return context

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        if not self._verifier_acces_groupe(instance):
            return Response(
                {"detail": "Vous n'êtes pas affecté à la promotion de ce groupe."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().retrieve(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if not self._verifier_acces_groupe(instance):
            return Response(
                {"detail": "Vous n'êtes pas affecté à la promotion de ce groupe."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if not self._verifier_acces_groupe(instance):
            return Response(
                {"detail": "Vous n'êtes pas affecté à la promotion de ce groupe."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().destroy(request, *args, **kwargs)

    def perform_create(self, serializer):
        """
        Vérifie que le formateur est bien affecté à la promotion du groupe créé.
        """
        user = self.request.user
        tenant_id = self.kwargs.get("tenant_id")
        promotion = serializer.validated_data.get("promotion")

        if not _est_admin_organisme(user, tenant_id):
            if not _est_formateur_de_promotion(user, promotion.id):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied(
                    "Vous n'êtes pas affecté à cette promotion."
                )

        serializer.save()

    @action(detail=True, methods=["post"], url_path="ajouter-apprenant")
    def ajouter_apprenant(self, request, tenant_id=None, pk=None):
        groupe = self.get_object()

        if not self._verifier_acces_groupe(groupe):
            return Response(
                {"detail": "Vous n'êtes pas affecté à la promotion de ce groupe."},
                status=status.HTTP_403_FORBIDDEN,
            )

        apprenant_id = request.data.get("apprenant_id")
        if not apprenant_id:
            return Response(
                {"apprenant_id": "Ce champ est obligatoire."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        apprenant = get_object_or_404(Utilisateur, pk=apprenant_id)

        # Rôle APPRENANT actif dans le même tenant
        # NOTE : Utilisateur.actif n'est PAS vérifié — seul MembreTenant.actif l'est.
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

        # Inscription active dans LA PROMOTION DU GROUPE
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
                {
                    "apprenant": (
                        f"L'apprenant appartient à la promotion '{inscription.promotion.nom}' "
                        f"et ne peut pas être ajouté à un groupe de la promotion "
                        f"'{groupe.promotion.nom}'."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Déjà membre du groupe
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

        if not self._verifier_acces_groupe(groupe):
            return Response(
                {"detail": "Vous n'êtes pas affecté à la promotion de ce groupe."},
                status=status.HTTP_403_FORBIDDEN,
            )

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
