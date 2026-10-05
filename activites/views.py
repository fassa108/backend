import os

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Prefetch, Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import MembreTenant
from pedagogie.models import CompetenceNiveau, InscriptionPromotion, Promotion
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
    CommentairePair,
    Evaluation,
    FichierLivrable,
    Livrable,
    Ressource,
)
from . import progression, tableau_de_bord
from .apercus import demander_apercu, reponse_consultation
from .notifications import (
    notifier_assignations,
    notifier_commentaire,
    notifier_depot,
    notifier_evaluation,
    notifier_publication,
)
from .permissions import (
    CanCreateLivrable,
    CanManageRessource,
    IsFormateurOrganisme,
    IsMembreOrganisme,
)
from .serializers import (
    AssignationSerializer,
    BriefSerializer,
    CategorieBriefSerializer,
    CommentairePairSerializer,
    DepotSerializer,
    EvaluationSerializer,
    FichierLivrableSerializer,
    LivrableSerializer,
    RessourceSerializer,
    brief_a_des_livrables,
    brief_ouvert_aux_commentaires,
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


# ─── Limites des fichiers ─────────────────────────────────────────────────────

class LimitesFichiersView(APIView):
    """
    Limites des fichiers déposés, lues par le frontend pour ses contrôles
    et ses textes d'aide : le réglage reste à un seul endroit (settings).
    """

    def get(self, request):
        return Response({"taille_max_fichier_mo": settings.TAILLE_MAX_FICHIER_MO})


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
        if self.action in ACTIONS_LECTURE + ("telecharger", "consulter"):
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
        # Sur le disque, le nom est aléatoire : on propose « titre.extension »
        ext = os.path.splitext(ressource.fichier.name)[1]
        return FileResponse(fichier, as_attachment=True, filename=f"{ressource.titre}{ext}")

    @action(detail=True, methods=["get"], url_path="consulter")
    def consulter(self, request, tenant_id=None, pk=None):
        """Affichage dans la plateforme (PDF, texte, ou aperçu PDF d'un fichier Office)."""
        return reponse_consultation(self.get_object())

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
        ressource = serializer.save(tenant=tenant, formateur=self.request.user)
        demander_apercu(ressource)

    def perform_update(self, serializer):
        ressource = serializer.instance
        self._verifier_proprietaire(ressource)
        # Nouveau fichier ou passage à un lien : l'ancien fichier et son
        # aperçu partent, une fois la modification enregistrée.
        change_de_source = "fichier" in serializer.validated_data or serializer.validated_data.get("url")
        anciens = [f for f in (ressource.fichier, ressource.apercu) if f] if change_de_source else []
        anciens = [(f.storage, f.name) for f in anciens]
        if change_de_source:
            ressource.apercu = None
            ressource.apercu_statut = ""
        ressource = serializer.save()
        for stockage, nom in anciens:
            transaction.on_commit(lambda s=stockage, n=nom: s.delete(n))
        if change_de_source:
            demander_apercu(ressource)

    def perform_destroy(self, instance):
        self._verifier_proprietaire(instance)
        if instance.briefs.exists():
            raise PermissionDenied(
                "Cette ressource est jointe à des briefs : retirez-la d'abord de ces briefs."
            )
        fichiers = [(f.storage, f.name) for f in (instance.fichier, instance.apercu) if f]
        instance.delete()
        for stockage, nom in fichiers:
            transaction.on_commit(lambda s=stockage, n=nom: s.delete(n))


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
            .prefetch_related(
                Prefetch(
                    "competence_niveaux",
                    queryset=CompetenceNiveau.objects.select_related("competence", "niveau")
                    .order_by("competence__ordre", "niveau__ordre"),
                ),
                "ressources",
            )
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

    def perform_update(self, serializer):
        etait_brouillon = serializer.instance.statut == Brief.Statut.BROUILLON
        brief = serializer.save()
        # Publication d'un brouillon : les apprenants déjà assignés sont prévenus
        if etait_brouillon and brief.statut == Brief.Statut.PUBLIE:
            notifier_publication(brief)

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

        notifier_assignations(creees)
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

    def perform_create(self, serializer):
        notifier_assignations([serializer.save()])

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
    """
    Filtre un queryset de livrables (ou de fichiers via « prefixe ») selon le rôle.

    Apprenant : tous les dépôts de ses assignations ; et, sur chaque brief où
    il a déjà déposé, le dernier dépôt de chacun des autres (travaux des pairs).
    """
    role = _role(user, tenant_id)
    if role == MembreTenant.Role.ADMINISTRATEUR:
        return qs
    if role == MembreTenant.Role.FORMATEUR:
        return qs.filter(**{
            f"{prefixe}assignation__brief__promotion_id__in": _promotions_du_formateur(user, tenant_id)
        })

    siens = Livrable.objects.filter(
        _assignations_de_l_apprenant(user, tenant_id, prefixe="assignation__")
    )
    briefs_deposes = siens.values_list("assignation__brief_id", flat=True)
    derniers_des_pairs = (
        Livrable.objects.filter(assignation__brief_id__in=briefs_deposes)
        .order_by()  # sans le tri par défaut, qui fausserait le regroupement
        .values("assignation_id")
        .annotate(dernier=Max("id"))
        .values_list("dernier", flat=True)
    )
    return qs.filter(
        Q(**{f"{prefixe}id__in": siens.values_list("id", flat=True)})
        | Q(**{f"{prefixe}id__in": list(derniers_des_pairs)})
    ).distinct()


class LivrableViewSet(viewsets.ModelViewSet):
    """
    Dépôts (« Dépôt n°1, n°2… ») : jamais modifiés ni supprimés.

    - Apprenant : dépôt (fichiers et liens en une requête) ; lecture de ses
      dépôts, et du dernier dépôt de ses pairs sur les briefs où il a déposé.
    - Formateur : lecture des dépôts de ses promotions.
    - Admin organisme : lecture de tous les dépôts du tenant.
    - Admin SaaS : aucun accès.

    Filtres : ?brief=, ?assignation=, ?module=.
    """

    serializer_class = LivrableSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        if self.action in ACTIONS_LECTURE:
            return [IsMembreOrganisme()]
        if self.action == "create":
            return [CanCreateLivrable()]
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
                "assignation__groupe",
                "assignation__apprenant",
                "deposant",
            )
            .prefetch_related("fichiers")
        )
        brief_id = _param_entier(self.request, "brief")
        if brief_id is not None:
            qs = qs.filter(assignation__brief_id=brief_id)
        assignation_id = _param_entier(self.request, "assignation")
        if assignation_id is not None:
            qs = qs.filter(assignation_id=assignation_id)
        module_id = _param_entier(self.request, "module")
        if module_id is not None:
            qs = qs.filter(assignation__brief__module_id=module_id)
        return _livrables_visibles(self.request.user, tenant_id, qs)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs["tenant_id"]
        context["request"] = self.request
        context["role"] = _role(self.request.user, self.kwargs["tenant_id"])
        return context

    def create(self, request, *args, **kwargs):
        serializer = DepotSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        livrable = serializer.save()
        notifier_depot(livrable)
        return Response(LivrableSerializer(livrable).data, status=status.HTTP_201_CREATED)


class FichierLivrableViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Éléments des dépôts, en lecture. Ils sont créés avec le dépôt ; un fichier
    se consulte (« consulter ») ou se télécharge (« telecharger ») via des
    routes qui vérifient les droits.
    """

    serializer_class = FichierLivrableSerializer
    http_method_names = ["get", "head", "options"]

    def get_permissions(self):
        if self.action in ACTIONS_LECTURE + ("telecharger", "consulter"):
            return [IsMembreOrganisme()]
        return _refus_par_defaut(self)

    @action(detail=True, methods=["get"], url_path="telecharger")
    def telecharger(self, request, tenant_id=None, pk=None):
        element = self.get_object()
        if not element.fichier:
            raise NotFound("Cet élément est un lien, pas un fichier.")
        try:
            fichier = element.fichier.open("rb")
        except FileNotFoundError:
            raise NotFound("Le fichier est introuvable.")
        return FileResponse(fichier, as_attachment=True, filename=element.nom)

    @action(detail=True, methods=["get"], url_path="consulter")
    def consulter(self, request, tenant_id=None, pk=None):
        """Affichage dans la plateforme (PDF, texte, ou aperçu PDF d'un fichier Office)."""
        return reponse_consultation(self.get_object())

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


# ─── Évaluations ──────────────────────────────────────────────────────────────

class EvaluationViewSet(viewsets.ModelViewSet):
    """
    Évaluations des rendus : créées, jamais modifiées ni supprimées (une
    nouvelle évaluation remplace la précédente, l'historique est gardé).

    - Formateur : évalue les rendus des briefs qu'il a créés (ou de sa
      promotion si le créateur n'y est plus affecté) ; lit celles de ses
      promotions.
    - Admin organisme : lecture de tout le tenant.
    - Apprenant : lecture des évaluations de ses rendus (et de ses groupes).

    Filtres : ?brief=, ?assignation=.
    """

    serializer_class = EvaluationSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        if self.action in ACTIONS_LECTURE:
            return [IsMembreOrganisme()]
        if self.action == "create":
            return [IsFormateurOrganisme()]
        return _refus_par_defaut(self)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs["tenant_id"]
        return context

    def get_queryset(self):
        tenant_id = self.kwargs["tenant_id"]
        user = self.request.user
        qs = (
            Evaluation.objects.filter(assignation__brief__promotion__formation__tenant_id=tenant_id)
            .select_related("assignation", "assignation__groupe", "assignation__apprenant", "evaluateur")
            .prefetch_related("competences__competence_niveau__competence", "competences__competence_niveau__niveau")
        )
        brief_id = _param_entier(self.request, "brief")
        if brief_id is not None:
            qs = qs.filter(assignation__brief_id=brief_id)
        assignation_id = _param_entier(self.request, "assignation")
        if assignation_id is not None:
            qs = qs.filter(assignation_id=assignation_id)

        role = _role(user, tenant_id)
        if role == MembreTenant.Role.ADMINISTRATEUR:
            return qs
        if role == MembreTenant.Role.FORMATEUR:
            return qs.filter(assignation__brief__promotion_id__in=_promotions_du_formateur(user, tenant_id))
        return qs.filter(_assignations_de_l_apprenant(user, tenant_id, prefixe="assignation__")).distinct()

    def perform_create(self, serializer):
        evaluation = serializer.save(evaluateur=self.request.user)
        notifier_evaluation(evaluation)


# ─── Progression ──────────────────────────────────────────────────────────────

class ProgressionViewSet(viewsets.ViewSet):
    """
    Progression des apprenants (compétences validées, briefs validés).

    - list ?promotion= : résumé par apprenant inscrit (formateur de la
      promotion ou admin organisme).
    - retrieve <apprenant> : détail (référentiel et briefs) ; l'apprenant ne
      voit que la sienne, le formateur celles de ses promotions.
    - moi : raccourci de l'apprenant connecté.
    """

    permission_classes = [IsMembreOrganisme]

    def _promotions_autorisees(self, tenant_id):
        """None = toutes (admin) ; sinon les promotions du formateur ; vide pour un apprenant."""
        user = self.request.user
        role = _role(user, tenant_id)
        if role == MembreTenant.Role.ADMINISTRATEUR:
            return None
        if role == MembreTenant.Role.FORMATEUR:
            return set(_promotions_du_formateur(user, tenant_id))
        return set()

    def list(self, request, tenant_id=None):
        promotion_id = _param_entier(request, "promotion")
        if promotion_id is None:
            raise NotFound("Précisez la promotion (?promotion=).")
        autorisees = self._promotions_autorisees(tenant_id)
        if autorisees is not None and promotion_id not in autorisees:
            raise NotFound("Promotion introuvable.")
        promotion = get_object_or_404(Promotion, pk=promotion_id, formation__tenant_id=tenant_id)
        inscriptions = (
            InscriptionPromotion.objects.filter(promotion=promotion, actif=True)
            .select_related("apprenant")
            .order_by("apprenant__nom", "apprenant__prenom")
        )
        return Response([
            {
                "apprenant": i.apprenant_id,
                "nom": f"{i.apprenant.prenom} {i.apprenant.nom}",
                **progression.resume(i.apprenant, promotion),
            }
            for i in inscriptions
        ])

    def retrieve(self, request, tenant_id=None, pk=None):
        user = request.user
        inscription = (
            InscriptionPromotion.objects.filter(apprenant_id=pk, tenant_id=tenant_id, actif=True)
            .select_related("apprenant", "promotion")
            .first()
        )
        if inscription is None:
            raise NotFound("Apprenant introuvable.")
        if _role(user, tenant_id) == MembreTenant.Role.APPRENANT:
            if inscription.apprenant_id != user.id:
                raise NotFound("Apprenant introuvable.")
        else:
            autorisees = self._promotions_autorisees(tenant_id)
            if autorisees is not None and inscription.promotion_id not in autorisees:
                raise NotFound("Apprenant introuvable.")
        return Response(self._detail(inscription))

    @action(detail=False, methods=["get"])
    def moi(self, request, tenant_id=None):
        inscription = progression.inscription_active(request.user, tenant_id)
        if inscription is None:
            raise NotFound("Vous n'êtes inscrit à aucune promotion ouverte.")
        return Response(self._detail(inscription))

    def _detail(self, inscription):
        a, p = inscription.apprenant, inscription.promotion
        return {
            "apprenant": {"id": a.id, "nom": f"{a.prenom} {a.nom}"},
            "promotion": {"id": p.id, "nom": p.nom},
            **progression.detail(a, p),
        }


# ─── Feedback entre pairs ─────────────────────────────────────────────────────

class EstApprenantOrganisme(IsMembreOrganisme):
    message = "Seuls les apprenants commentent les travaux de leurs pairs."

    def has_permission(self, request, view):
        return (
            super().has_permission(request, view)
            and _role(request.user, view.kwargs["tenant_id"]) == MembreTenant.Role.APPRENANT
        )


class CommentairePairViewSet(viewsets.ModelViewSet):
    """
    Commentaires des apprenants sur les rendus de leurs pairs (et réponses).

    - Apprenant : lit les commentaires des rendus qu'il peut voir (les siens,
      et ceux de ses pairs sur les briefs où il a déposé) ; commente, répond,
      modifie ou supprime les siens. Un commentaire masqué n'est plus visible
      que de son auteur (et des formateurs).
    - Formateur : lit ceux de ses promotions, masque / démasque.
    - Admin organisme : lecture.

    Filtres : ?brief=, ?assignation=.
    """

    serializer_class = CommentairePairSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ACTIONS_LECTURE:
            return [IsMembreOrganisme()]
        if self.action in ("create", "partial_update", "destroy"):
            return [EstApprenantOrganisme()]
        if self.action in ("masquer", "demasquer"):
            return [IsFormateurOrganisme()]
        return _refus_par_defaut(self)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs["tenant_id"]
        return context

    def get_queryset(self):
        tenant_id = self.kwargs["tenant_id"]
        user = self.request.user
        qs = CommentairePair.objects.filter(
            assignation__brief__promotion__formation__tenant_id=tenant_id
        ).select_related("auteur", "assignation__brief__promotion")
        brief_id = _param_entier(self.request, "brief")
        if brief_id is not None:
            qs = qs.filter(assignation__brief_id=brief_id)
        assignation_id = _param_entier(self.request, "assignation")
        if assignation_id is not None:
            qs = qs.filter(assignation_id=assignation_id)

        role = _role(user, tenant_id)
        if role == MembreTenant.Role.ADMINISTRATEUR:
            return qs
        if role == MembreTenant.Role.FORMATEUR:
            return qs.filter(assignation__brief__promotion_id__in=_promotions_du_formateur(user, tenant_id))

        siens = _assignations_de_l_apprenant(user, tenant_id, prefixe="assignation__")
        briefs_deposes = Livrable.objects.filter(siens).values_list("assignation__brief_id", flat=True)
        des_pairs = Q(
            assignation__brief_id__in=list(briefs_deposes),
            assignation__livrables__isnull=False,
        )
        return (
            qs.filter(siens | des_pairs)
            .exclude(Q(masque=True) & ~Q(auteur=user))
            .exclude(Q(parent__masque=True) & ~Q(auteur=user))
            .distinct()
        )

    def perform_create(self, serializer):
        commentaire = serializer.save(auteur=self.request.user)
        notifier_commentaire(commentaire)

    def _verifier_auteur(self, commentaire):
        if commentaire.auteur_id != self.request.user.id:
            raise PermissionDenied("Seul l'auteur peut modifier ou supprimer son commentaire.")

    def perform_update(self, serializer):
        self._verifier_auteur(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._verifier_auteur(instance)
        if not brief_ouvert_aux_commentaires(instance.assignation.brief):
            raise PermissionDenied("Ce brief est fermé : les commentaires sont en lecture seule.")
        instance.delete()

    def _changer_masque(self, masque):
        commentaire = self.get_object()
        if not _est_formateur_de_promotion(self.request.user, commentaire.assignation.brief.promotion_id):
            raise PermissionDenied("Seul un formateur de la promotion peut masquer un commentaire.")
        commentaire.masque = masque
        commentaire.masque_par = self.request.user if masque else None
        commentaire.save(update_fields=["masque", "masque_par"])
        return Response(self.get_serializer(commentaire).data)

    @action(detail=True, methods=["post"])
    def masquer(self, request, tenant_id=None, pk=None):
        return self._changer_masque(True)

    @action(detail=True, methods=["post"])
    def demasquer(self, request, tenant_id=None, pk=None):
        return self._changer_masque(False)


# ─── Tableaux de bord ─────────────────────────────────────────────────────────
# Une route par rôle, qui calcule tout en une fois (activites/tableau_de_bord.py).

class TableauDeBordFormateurView(APIView):
    """Rendus à évaluer, échéances, suivi des briefs, apprenants à suivre."""

    permission_classes = [IsFormateurOrganisme]

    def get(self, request, tenant_id):
        return Response(tableau_de_bord.tableau_de_bord_formateur(request.user, tenant_id))


class TableauDeBordAdminView(APIView):
    """Indicateurs de l'organisme, promotions, activité récente, points d'attention."""

    permission_classes = [IsAdminOrganisme]

    def get(self, request, tenant_id):
        return Response(tableau_de_bord.tableau_de_bord_admin(tenant_id))


class TableauDeBordApprenantView(APIView):
    """Briefs à rendre, quiz à faire, progression, retours récents."""

    permission_classes = [EstApprenantOrganisme]

    def get(self, request, tenant_id):
        return Response(tableau_de_bord.tableau_de_bord_apprenant(request.user, tenant_id))
