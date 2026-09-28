import os

from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response

from accounts.models import MembreTenant
from pedagogie.permissions import IsAdminOrganisme, _est_formateur_de_promotion
from pedagogie.views import (
    _param_entier,
    _promotions_de_l_apprenant,
    _promotions_du_formateur,
)
from tenants.models import Tenant

from .models import (
    Assignation,
    Brief,
    CategorieBrief,
    FichierLivrable,
    Livrable,
    Ressource,
)
from .permissions import (
    CanCreateLivrable,
    CanManageRessource,
    CanUpdateLivrableStatut,
    IsFormateurOrganisme,
    IsMembreOrganisme,
)
from .serializers import (
    AssignationSerializer,
    BriefSerializer,
    CategorieBriefSerializer,
    FichierLivrableSerializer,
    LivrableSerializer,
    LivrableStatutSerializer,
    RessourceSerializer,
    brief_a_des_livrables,
)

ACTIONS_LECTURE = ("list", "retrieve")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _role(user, tenant_id):
    """Rôle actif de l'utilisateur dans le tenant (None s'il n'est pas membre)."""
    return MembreTenant.objects.filter(
        utilisateur=user, tenant_id=tenant_id, actif=True
    ).values_list("role", flat=True).first()


def _briefs_visibles_apprenant(user, tenant_id):
    """Briefs publiés ou archivés des promotions de l'apprenant (jamais les brouillons)."""
    return Q(
        promotion_id__in=list(_promotions_de_l_apprenant(user, tenant_id)),
        statut__in=[Brief.Statut.PUBLIE, Brief.Statut.ARCHIVE],
    )


def _assignations_de_l_apprenant(user, tenant_id, prefixe=""):
    """
    Assignations qui visent l'apprenant : directement, ou via un groupe dont
    il est membre actif ; sur un brief qu'il peut voir.
    « prefixe » permet d'appliquer le filtre depuis un modèle lié
    (ex. « assignation__ » depuis Livrable).
    """
    p = prefixe
    promotions = list(_promotions_de_l_apprenant(user, tenant_id))
    return (
        Q(**{f"{p}apprenant": user})
        | Q(**{f"{p}groupe__membres__apprenant": user, f"{p}groupe__membres__actif": True})
    ) & Q(**{
        f"{p}brief__promotion_id__in": promotions,
        f"{p}brief__statut__in": [Brief.Statut.PUBLIE, Brief.Statut.ARCHIVE],
    })


class Refus(IsAdminOrganisme):
    """Action non prévue : refusée (au lieu de n'appliquer aucune permission)."""

    def has_permission(self, request, view):
        return False


def _refus_par_defaut(view):
    """
    Action non prévue : refusée (audit S13 : ne jamais renvoyer « aucune
    permission »). Pour une méthode HTTP non exposée par la vue,
    on exige d'être membre, puis DRF répond 405.
    """
    if view.request.method.lower() not in view.http_method_names:
        return [IsMembreOrganisme()]
    return [Refus()]


# ─── Ressources ───────────────────────────────────────────────────────────────

class RessourceViewSet(viewsets.ModelViewSet):
    """
    Bibliothèque de ressources de l'organisme (fichiers ou liens).

    - Admin organisme, Formateur : toute la bibliothèque ; création.
    - Modification / suppression : créateur de la ressource ou admin organisme.
    - Apprenant : uniquement les ressources jointes à ses briefs.
    - Admin SaaS : aucun accès.
    """

    serializer_class = RessourceSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ACTIONS_LECTURE + ("telecharger",):
            return [IsMembreOrganisme()]
        return [CanManageRessource()]

    @action(detail=True, methods=["get"], url_path="telecharger")
    def telecharger(self, request, tenant_id=None, pk=None):
        """
        Téléchargement du fichier d'une ressource, réservé à ceux qui peuvent
        la voir (audit S6 : les fichiers ne sont pas servis publiquement).
        """
        ressource = self.get_object()
        if not ressource.fichier:
            raise NotFound("Cette ressource est un lien, pas un fichier.")
        try:
            fichier = ressource.fichier.open("rb")
        except FileNotFoundError:
            raise NotFound("Le fichier de cette ressource est introuvable.")
        return FileResponse(
            fichier,
            as_attachment=True,
            filename=os.path.basename(ressource.fichier.name),
        )

    def get_queryset(self):
        tenant_id = self.kwargs["tenant_id"]
        user = self.request.user
        qs = Ressource.objects.filter(tenant_id=tenant_id).select_related(
            "formateur"
        ).order_by("-date_creation")

        if _role(user, tenant_id) == MembreTenant.Role.APPRENANT:
            briefs = Brief.objects.filter(_briefs_visibles_apprenant(user, tenant_id))
            qs = qs.filter(briefs__in=briefs).distinct()
        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs["tenant_id"]
        return context

    def _verifier_proprietaire(self, ressource):
        user = self.request.user
        tenant_id = self.kwargs["tenant_id"]
        if ressource.formateur_id != user.id and _role(user, tenant_id) != MembreTenant.Role.ADMINISTRATEUR:
            raise PermissionDenied(
                "Seuls le créateur de la ressource et l'administrateur peuvent la modifier."
            )

    def perform_create(self, serializer):
        tenant = get_object_or_404(Tenant, pk=self.kwargs["tenant_id"])
        serializer.save(tenant=tenant, formateur=self.request.user)

    def perform_update(self, serializer):
        self._verifier_proprietaire(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._verifier_proprietaire(instance)
        if instance.briefs.exists():
            raise PermissionDenied(
                "Cette ressource est jointe à des briefs : retirez-la d'abord de ces briefs."
            )
        instance.delete()


# ─── Catégories de brief ─────────────────────────────────────────────────────

class CategorieBriefViewSet(viewsets.ModelViewSet):
    """
    Catégories propres à l'organisme (classement des briefs).
    Lecture : tout membre actif. Gestion : Admin organisme.
    Une catégorie utilisée ne se supprime pas : on la désactive.
    """

    serializer_class = CategorieBriefSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ACTIONS_LECTURE:
            return [IsMembreOrganisme()]
        return [IsAdminOrganisme()]

    def get_queryset(self):
        return CategorieBrief.objects.filter(tenant_id=self.kwargs["tenant_id"])

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs["tenant_id"]
        return context

    def perform_create(self, serializer):
        serializer.save(tenant=get_object_or_404(Tenant, pk=self.kwargs["tenant_id"]))

    def perform_destroy(self, instance):
        if instance.briefs.exists():
            raise PermissionDenied(
                "Cette catégorie est utilisée par des briefs. Désactivez-la à la place."
            )
        instance.delete()


# ─── Briefs ───────────────────────────────────────────────────────────────────

class BriefViewSet(viewsets.ModelViewSet):
    """
    - Formateur : briefs de ses promotions ; création et gestion.
    - Admin organisme : lecture de tous les briefs du tenant.
    - Apprenant : briefs publiés ou archivés de ses promotions.
    - Admin SaaS : aucun accès.

    Un brief est figé dès le premier livrable (seul son statut change encore)
    et ne peut alors plus être supprimé.
    """

    serializer_class = BriefSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ACTIONS_LECTURE:
            return [IsMembreOrganisme()]
        if self.action in ("create", "partial_update", "destroy"):
            return [IsFormateurOrganisme()]
        return _refus_par_defaut(self)

    def get_queryset(self):
        tenant_id = self.kwargs["tenant_id"]
        user = self.request.user

        qs = (
            Brief.objects.filter(promotion__formation__tenant_id=tenant_id)
            .select_related("promotion", "promotion__formation", "module", "cree_par", "categorie")
            .prefetch_related("competence_niveaux", "ressources")
            .order_by("-date_creation")
        )

        promotion_id = _param_entier(self.request, "promotion")
        if promotion_id is not None:
            qs = qs.filter(promotion_id=promotion_id)
        statut = self.request.query_params.get("statut")
        if statut:
            qs = qs.filter(statut=statut)
        categorie_id = _param_entier(self.request, "categorie")
        if categorie_id is not None:
            qs = qs.filter(categorie_id=categorie_id)

        role = _role(user, tenant_id)
        if role == MembreTenant.Role.ADMINISTRATEUR:
            return qs
        if role == MembreTenant.Role.FORMATEUR:
            return qs.filter(promotion_id__in=_promotions_du_formateur(user, tenant_id))
        return qs.filter(_briefs_visibles_apprenant(user, tenant_id))

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs["tenant_id"]
        context["request"] = self.request
        return context

    def perform_create(self, serializer):
        serializer.save(cree_par=self.request.user)

    def perform_destroy(self, instance):
        if not _est_formateur_de_promotion(self.request.user, instance.promotion_id):
            raise PermissionDenied("Vous n'êtes pas affecté à la promotion de ce brief.")
        if not instance.promotion.actif:
            raise PermissionDenied("Cette promotion est clôturée : elle est en lecture seule.")
        if brief_a_des_livrables(instance):
            raise PermissionDenied(
                "Ce brief ne peut pas être supprimé car il possède des livrables."
            )
        instance.delete()


# ─── Assignations ─────────────────────────────────────────────────────────────

class AssignationViewSet(viewsets.ModelViewSet):
    """
    - Formateur : assignations de ses promotions ; création et suppression.
    - Admin organisme : lecture de toutes les assignations du tenant.
    - Apprenant : ses assignations (directes ou via un groupe dont il est
      membre actif), sur des briefs publiés ou archivés.

    Une assignation ne se modifie pas (la supprimer puis en recréer une) ;
    elle ne peut plus être supprimée dès qu'elle a des livrables.
    """

    serializer_class = AssignationSerializer
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ACTIONS_LECTURE:
            return [IsMembreOrganisme()]
        if self.action in ("create", "destroy", "multiple"):
            return [IsFormateurOrganisme()]
        return _refus_par_defaut(self)

    @action(detail=False, methods=["post"], url_path="multiple")
    def multiple(self, request, tenant_id=None):
        """
        Assignation de plusieurs cibles en une fois, « tout ou rien » :
        { "brief": id, "groupes": [ids], "apprenants": [ids] }.
        Chaque cible passe par les mêmes contrôles qu'une assignation seule
        (dans l'ordre : groupes puis apprenants, ce qui détecte un apprenant
        choisi en même temps que son groupe). Une seule erreur annule tout.
        """
        brief = request.data.get("brief")
        cibles = [("groupe", i) for i in request.data.get("groupes", [])] + [
            ("apprenant", i) for i in request.data.get("apprenants", [])
        ]
        if not cibles:
            return Response(
                {"detail": "Choisissez au moins un apprenant ou un groupe."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        erreurs, creees = [], []

        class _Annulation(Exception):
            pass

        try:
            with transaction.atomic():
                for champ, identifiant in cibles:
                    serializer = AssignationSerializer(
                        data={"brief": brief, champ: identifiant},
                        context=self.get_serializer_context(),
                    )
                    if serializer.is_valid():
                        creees.append(serializer.save())
                    else:
                        message = next(iter(serializer.errors.values()))
                        erreurs.append({
                            "type": champ,
                            "id": identifiant,
                            "message": message[0] if isinstance(message, list) else message,
                        })
                if erreurs:
                    raise _Annulation()
        except _Annulation:
            return Response(
                {"detail": "Aucune assignation n'a été créée.", "erreurs": erreurs},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            AssignationSerializer(creees, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    def get_queryset(self):
        tenant_id = self.kwargs["tenant_id"]
        user = self.request.user

        qs = Assignation.objects.filter(
            brief__promotion__formation__tenant_id=tenant_id
        ).select_related(
            "brief",
            "brief__promotion",
            "brief__promotion__formation",
            "groupe",
            "apprenant",
        )

        brief_id = _param_entier(self.request, "brief")
        if brief_id is not None:
            qs = qs.filter(brief_id=brief_id)

        role = _role(user, tenant_id)
        if role == MembreTenant.Role.ADMINISTRATEUR:
            return qs
        if role == MembreTenant.Role.FORMATEUR:
            return qs.filter(brief__promotion_id__in=_promotions_du_formateur(user, tenant_id))
        return qs.filter(_assignations_de_l_apprenant(user, tenant_id)).distinct()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs["tenant_id"]
        context["request"] = self.request
        return context

    def perform_destroy(self, instance):
        promotion = instance.brief.promotion
        if not _est_formateur_de_promotion(self.request.user, promotion.id):
            raise PermissionDenied("Vous n'êtes pas affecté à la promotion de ce brief.")
        if not promotion.actif:
            raise PermissionDenied("Cette promotion est clôturée : elle est en lecture seule.")
        if instance.livrables.exists():
            raise PermissionDenied(
                "Cette assignation a des livrables : elle ne peut pas être supprimée."
            )
        instance.delete()


# ─── Livrables ────────────────────────────────────────────────────────────────

def _livrables_visibles(user, tenant_id, qs, prefixe=""):
    """Filtre un queryset de livrables (ou de fichiers via « prefixe ») selon le rôle."""
    role = _role(user, tenant_id)
    if role == MembreTenant.Role.ADMINISTRATEUR:
        return qs
    if role == MembreTenant.Role.FORMATEUR:
        return qs.filter(**{
            f"{prefixe}assignation__brief__promotion_id__in": _promotions_du_formateur(user, tenant_id)
        })
    # Apprenant : les livrables de ses propres assignations
    return qs.filter(
        _assignations_de_l_apprenant(user, tenant_id, prefixe=f"{prefixe}assignation__")
    ).distinct()


class LivrableViewSet(viewsets.ModelViewSet):
    """
    - Apprenant : dépôt ; lecture des livrables de ses assignations.
    - Formateur : lecture des livrables de ses promotions.
    - Admin organisme : lecture de tous les livrables du tenant.
    - Admin SaaS : aucun accès.
    """

    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_permissions(self):
        if self.action in ACTIONS_LECTURE:
            return [IsMembreOrganisme()]
        if self.action == "create":
            return [CanCreateLivrable()]
        if self.action == "partial_update":
            return [CanUpdateLivrableStatut()]
        return _refus_par_defaut(self)

    def get_queryset(self):
        tenant_id = self.kwargs["tenant_id"]
        qs = (
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
        return _livrables_visibles(self.request.user, tenant_id, qs)

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


class FichierLivrableViewSet(viewsets.ModelViewSet):
    serializer_class = FichierLivrableSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        if self.action in ACTIONS_LECTURE:
            return [IsMembreOrganisme()]
        if self.action == "create":
            return [CanCreateLivrable()]
        return _refus_par_defaut(self)

    def get_queryset(self):
        tenant_id = self.kwargs["tenant_id"]
        qs = FichierLivrable.objects.filter(
            livrable__assignation__brief__promotion__formation__tenant_id=tenant_id
        ).select_related(
            "livrable",
            "livrable__assignation",
            "livrable__assignation__brief",
            "livrable__assignation__brief__promotion",
            "livrable__assignation__brief__promotion__formation",
        )
        return _livrables_visibles(self.request.user, tenant_id, qs, prefixe="livrable__")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs["tenant_id"]
        context["request"] = self.request
        return context
