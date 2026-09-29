from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
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
    IsFormateurOrganisme,
    IsMembreOrganisme,
    _est_admin_organisme,
    _est_formateur_actif,
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
from .services import InscriptionService

Utilisateur = get_user_model()

ACTIONS_LECTURE = ("list", "retrieve")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _promotions_du_formateur(user, tenant_id):
    """Retourne les IDs des promotions auxquelles le formateur est affecté."""
    return FormateurPromotion.objects.filter(
        formateur=user,
        promotion__formation__tenant_id=tenant_id,
    ).values_list("promotion_id", flat=True)


def _promotions_de_l_apprenant(user, tenant_id):
    """
    IDs des promotions de l'apprenant : inscription active, ou fermée par
    la clôture de la promotion (consultable en lecture seule).
    """
    return InscriptionPromotion.objects.filter(
        Q(actif=True) | Q(fermee_par_cloture=True),
        apprenant=user,
        tenant_id=tenant_id,
    ).values_list("promotion_id", flat=True)


def _promotions_visibles(user, tenant_id):
    """
    None pour l'admin organisme (tout le tenant), sinon la liste des IDs
    de promotions visibles : affectations (formateur) ou inscriptions
    (apprenant).
    """
    if _est_admin_organisme(user, tenant_id):
        return None
    if _est_formateur_actif(user, tenant_id):
        return list(_promotions_du_formateur(user, tenant_id))
    return list(_promotions_de_l_apprenant(user, tenant_id))


def _formations_visibles(user, tenant_id):
    """None pour l'admin organisme, sinon les IDs des formations visibles."""
    promotions = _promotions_visibles(user, tenant_id)
    if promotions is None:
        return None
    return Promotion.objects.filter(pk__in=promotions).values_list(
        "formation_id", flat=True
    )


def _param_entier(request, nom):
    """Paramètre d'URL entier facultatif ; 400 s'il n'est pas un entier."""
    valeur = request.query_params.get(nom)
    if valeur is None or valeur == "":
        return None
    try:
        return int(valeur)
    except ValueError:
        raise ValidationError({nom: "Doit être un identifiant numérique."})


def _verifier_promotion_ouverte(promotion):
    if not promotion.actif:
        raise PermissionDenied(
            "Cette promotion est clôturée : elle est consultable en lecture seule."
        )


def _refuser_suppression_si(condition, message):
    if condition:
        raise PermissionDenied(f"{message} Désactivez-le à la place.")


# ─── Référentiel (formations, modules, compétences, niveaux) ──────────────────

class ReferentielViewSet(viewsets.ModelViewSet):
    """
    Base des vues du référentiel d'une formation.

    - Admin organisme : CRUD complet sur son tenant.
    - Formateur : lecture des formations de ses promotions.
    - Apprenant : lecture de la formation de sa promotion.
    - Admin SaaS : aucun accès.
    """

    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ACTIONS_LECTURE:
            return [IsMembreOrganisme()]
        return [IsAdminOrganisme()]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs.get("tenant_id")
        return context

    def formations_visibles(self):
        return _formations_visibles(self.request.user, self.kwargs.get("tenant_id"))


# ─── Formations ───────────────────────────────────────────────────────────────

class FormationViewSet(ReferentielViewSet):
    serializer_class = FormationSerializer

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        qs = Formation.objects.filter(tenant_id=tenant_id).order_by("nom")

        visibles = self.formations_visibles()
        if visibles is not None:
            qs = qs.filter(pk__in=visibles)
        return qs

    def perform_create(self, serializer):
        tenant_id = self.kwargs.get("tenant_id")
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        serializer.save(tenant=tenant)

    def perform_destroy(self, instance):
        _refuser_suppression_si(
            instance.promotions.exists() or instance.modules.exists(),
            "Cette formation a des promotions ou des modules.",
        )
        instance.delete()


# ─── Promotions ───────────────────────────────────────────────────────────────

class PromotionViewSet(viewsets.ModelViewSet):
    """
    Admin organisme : CRUD, inscriptions, affectations, clôture / réouverture.
    Formateur : lecture de ses promotions, de leurs inscrits et formateurs.
    Apprenant : lecture de sa promotion.
    Admin SaaS : aucun accès.

    Une promotion clôturée (actif = False) est en lecture seule.
    """

    serializer_class = PromotionSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ACTIONS_LECTURE:
            return [IsMembreOrganisme()]
        if self.action in ("inscriptions", "formateurs"):
            return [IsAdminOrganismeOrFormateur()]
        # Écriture, inscriptions, affectations, clôture : Admin organisme
        return [IsAdminOrganisme()]

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")

        qs = Promotion.objects.filter(
            formation__tenant_id=tenant_id
        ).select_related("formation").order_by("-date_debut")

        formation_id = _param_entier(self.request, "formation")
        if formation_id is not None:
            qs = qs.filter(formation_id=formation_id)

        visibles = _promotions_visibles(self.request.user, tenant_id)
        if visibles is not None:
            qs = qs.filter(pk__in=visibles)
        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs.get("tenant_id")
        return context

    def perform_update(self, serializer):
        _verifier_promotion_ouverte(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        _refuser_suppression_si(
            instance.inscriptions.exists()
            or instance.groupes.exists()
            or instance.briefs.exists(),
            "Cette promotion a des inscriptions, des groupes ou des briefs.",
        )
        instance.delete()

    # ── Inscriptions ──────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"], url_path="inscrire-apprenant")
    def inscrire_apprenant(self, request, tenant_id=None, pk=None):
        promotion = self.get_object()
        _verifier_promotion_ouverte(promotion)

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

        # Une seule inscription active par organisme
        active_inscription = InscriptionPromotion.objects.filter(
            apprenant=apprenant,
            tenant=tenant,
            actif=True,
        ).select_related("promotion").first()

        if active_inscription:
            if active_inscription.promotion_id == promotion.id:
                return Response(
                    {"apprenant": "L'apprenant est déjà inscrit dans cette promotion."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
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
            existing.fermee_par_cloture = False
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
        _verifier_promotion_ouverte(promotion)

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

        InscriptionService.desinscrire(inscription)

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

    # ── Clôture ───────────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"], url_path="cloturer")
    def cloturer(self, request, tenant_id=None, pk=None):
        """Clôture la promotion et ferme ses inscriptions actives."""
        promotion = self.get_object()
        if not promotion.actif:
            return Response(
                {"detail": "Cette promotion est déjà clôturée."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        nb = InscriptionService.cloturer(promotion)
        return Response(
            {
                "detail": "Promotion clôturée.",
                "inscriptions_fermees": nb,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="rouvrir")
    def rouvrir(self, request, tenant_id=None, pk=None):
        """Rouvre la promotion et réactive les inscriptions fermées par la clôture."""
        promotion = self.get_object()
        if promotion.actif:
            return Response(
                {"detail": "Cette promotion n'est pas clôturée."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reactivees, non_reactives = InscriptionService.rouvrir(promotion)
        return Response(
            {
                "detail": "Promotion rouverte.",
                "inscriptions_reactivees": reactivees,
                # Apprenants inscrits entre-temps dans une autre promotion
                "non_reactives": non_reactives,
            },
            status=status.HTTP_200_OK,
        )

    # ── Formateurs ────────────────────────────────────────────────────────────

    @action(
        detail=True,
        methods=["post"],
        url_path="affecter-formateur",
    )
    def affecter_formateur(self, request, tenant_id=None, pk=None):
        """Affecter un formateur à une promotion. Admin organisme uniquement."""
        promotion = self.get_object()
        _verifier_promotion_ouverte(promotion)

        # Injecter la promotion depuis l'URL si non fournie dans le body
        data = request.data.copy()
        data.setdefault("promotion", promotion.id)
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
        """
        Retirer un formateur d'une promotion. Admin organisme uniquement.
        Les briefs appartiennent à la promotion : ils restent en place.
        """
        promotion = self.get_object()
        _verifier_promotion_ouverte(promotion)

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

class ModuleViewSet(ReferentielViewSet):
    serializer_class = ModuleSerializer

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        qs = Module.objects.filter(
            formation__tenant_id=tenant_id
        ).select_related("formation").order_by("ordre", "nom")

        formation_id = _param_entier(self.request, "formation")
        if formation_id is not None:
            qs = qs.filter(formation_id=formation_id)

        visibles = self.formations_visibles()
        if visibles is not None:
            qs = qs.filter(formation_id__in=visibles)
        return qs

    def perform_destroy(self, instance):
        _refuser_suppression_si(
            instance.competences.exists() or instance.briefs.exists(),
            "Ce module a des compétences ou est utilisé par des briefs.",
        )
        instance.delete()


# ─── Compétences ──────────────────────────────────────────────────────────────

class CompetenceViewSet(ReferentielViewSet):
    serializer_class = CompetenceSerializer

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        qs = Competence.objects.filter(
            module__formation__tenant_id=tenant_id
        ).select_related("module", "module__formation").order_by("ordre", "nom")

        module_id = _param_entier(self.request, "module")
        if module_id is not None:
            qs = qs.filter(module_id=module_id)

        visibles = self.formations_visibles()
        if visibles is not None:
            qs = qs.filter(module__formation_id__in=visibles)
        return qs

    def perform_destroy(self, instance):
        _refuser_suppression_si(
            instance.niveaux.exists(),
            "Cette compétence est décrite par niveau.",
        )
        instance.delete()


# ─── Niveaux ──────────────────────────────────────────────────────────────────

class NiveauViewSet(ReferentielViewSet):
    """Les niveaux sont définis pour tout l'organisme : visibles par tous ses membres."""

    serializer_class = NiveauSerializer

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        return Niveau.objects.filter(tenant_id=tenant_id).order_by("ordre", "nom")

    def perform_create(self, serializer):
        tenant_id = self.kwargs.get("tenant_id")
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        serializer.save(tenant=tenant)

    def perform_destroy(self, instance):
        _refuser_suppression_si(
            instance.competences.exists(),
            "Ce niveau est utilisé pour décrire des compétences.",
        )
        instance.delete()


# ─── Compétence-Niveaux ───────────────────────────────────────────────────────

class CompetenceNiveauViewSet(ReferentielViewSet):
    serializer_class = CompetenceNiveauSerializer

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

        competence_id = _param_entier(self.request, "competence")
        if competence_id is not None:
            qs = qs.filter(competence_id=competence_id)
        niveau_id = _param_entier(self.request, "niveau")
        if niveau_id is not None:
            qs = qs.filter(niveau_id=niveau_id)

        visibles = self.formations_visibles()
        if visibles is not None:
            qs = qs.filter(competence__module__formation_id__in=visibles)
        return qs

    def perform_destroy(self, instance):
        # Suppression directe (pas de désactivation) : refusée si un brief
        # vise ce niveau de la compétence, ou s'il a déjà été évalué.
        if instance.briefs.exists():
            raise PermissionDenied(
                "Ce niveau de la compétence est visé par des briefs : "
                "il ne peut pas être retiré."
            )
        if instance.evaluations.exists() or instance.validations.exists():
            raise PermissionDenied(
                "Ce niveau de la compétence a déjà été évalué : "
                "il ne peut pas être retiré."
            )
        instance.delete()


# ─── Groupes ──────────────────────────────────────────────────────────────────

class GroupeViewSet(viewsets.ModelViewSet):
    """
    Formateur : CRUD sur les groupes de ses promotions affectées.
    Admin organisme : lecture de tous les groupes du tenant.
    Apprenant : lecture de ses groupes (et de leurs membres).
    Admin SaaS : aucun accès.

    Un groupe ne change pas de promotion. Les groupes d'une promotion
    clôturée sont en lecture seule.
    """

    serializer_class = GroupeSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ACTIONS_LECTURE:
            return [IsMembreOrganisme()]
        return [IsFormateurOrganisme()]

    def _verifier_formateur_du_groupe(self, promotion):
        """Écriture : formateur affecté à la promotion, promotion ouverte."""
        if not _est_formateur_de_promotion(self.request.user, promotion.id):
            raise PermissionDenied("Vous n'êtes pas affecté à la promotion de ce groupe.")
        _verifier_promotion_ouverte(promotion)

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

        promotion_id = _param_entier(self.request, "promotion")
        if promotion_id is not None:
            base_qs = base_qs.filter(promotion_id=promotion_id)

        # Admin organisme : tous les groupes du tenant
        if _est_admin_organisme(user, tenant_id):
            return base_qs

        # Formateur : groupes de ses promotions affectées
        if _est_formateur_actif(user, tenant_id):
            ids = _promotions_du_formateur(user, tenant_id)
            return base_qs.filter(promotion_id__in=ids)

        # Apprenant : groupes dont il est membre actif, dans ses promotions
        return base_qs.filter(
            promotion_id__in=list(_promotions_de_l_apprenant(user, tenant_id)),
            membres__apprenant=user,
            membres__actif=True,
        ).distinct()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs.get("tenant_id")
        return context

    def perform_create(self, serializer):
        self._verifier_formateur_du_groupe(serializer.validated_data["promotion"])
        serializer.save()

    def perform_update(self, serializer):
        self._verifier_formateur_du_groupe(serializer.instance.promotion)
        serializer.save()

    def perform_destroy(self, instance):
        self._verifier_formateur_du_groupe(instance.promotion)
        _refuser_suppression_si(
            instance.membres.exists() or instance.assignations.exists(),
            "Ce groupe a des membres ou des briefs assignés.",
        )
        instance.delete()

    @action(detail=True, methods=["post"], url_path="ajouter-apprenant")
    def ajouter_apprenant(self, request, tenant_id=None, pk=None):
        groupe = self.get_object()
        self._verifier_formateur_du_groupe(groupe.promotion)

        if not groupe.actif:
            return Response(
                {"detail": "Ce groupe est désactivé : réactivez-le pour y ajouter des apprenants."},
                status=status.HTTP_400_BAD_REQUEST,
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
        if not InscriptionPromotion.objects.filter(
            apprenant=apprenant,
            promotion=groupe.promotion,
            actif=True,
        ).exists():
            return Response(
                {
                    "apprenant": (
                        f"L'apprenant n'a pas d'inscription active dans la promotion "
                        f"'{groupe.promotion.nom}'."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        membre = GroupeMembre.objects.filter(groupe=groupe, apprenant=apprenant).first()
        if membre and membre.actif:
            return Response(
                {"apprenant": "Cet apprenant est déjà membre de ce groupe."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if membre:
            # Appartenance conservée après une désinscription : réactivée
            membre.actif = True
            membre.save(update_fields=["actif"])
            status_code = status.HTTP_200_OK
        else:
            membre = GroupeMembre.objects.create(groupe=groupe, apprenant=apprenant)
            status_code = status.HTTP_201_CREATED

        serializer = GroupeMembreSerializer(membre)
        return Response(serializer.data, status=status_code)

    @action(detail=True, methods=["post"], url_path="retirer-apprenant")
    def retirer_apprenant(self, request, tenant_id=None, pk=None):
        groupe = self.get_object()
        self._verifier_formateur_du_groupe(groupe.promotion)

        apprenant_id = request.data.get("apprenant_id")
        if not apprenant_id:
            return Response(
                {"apprenant_id": "Ce champ est obligatoire."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        membre = GroupeMembre.objects.filter(
            groupe=groupe,
            apprenant_id=apprenant_id,
            actif=True,
        ).first()
        if not membre:
            return Response(
                {"apprenant": "Cet apprenant n'est pas membre de ce groupe."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Livrables déposés dans la promotion : appartenance gardée, inactive
        if InscriptionService.a_depose_dans_promotion(apprenant_id, groupe.promotion_id):
            membre.actif = False
            membre.save(update_fields=["actif"])
        else:
            membre.delete()

        return Response(
            {"detail": "Apprenant retiré du groupe avec succès."},
            status=status.HTTP_200_OK,
        )
