from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from accounts.models import MembreTenant
from activites.models import Livrable
from activites.views import _assignations_de_l_apprenant, _refus_par_defaut, _role
from pedagogie.models import Promotion
from pedagogie.permissions import IsFormateurOrganisme, IsMembreOrganisme
from pedagogie.views import _param_entier, _promotions_du_formateur

from .models import Option, Question, SupportRevision, Tentative
from .serializers import (
    GenerationSerializer,
    RelectureSerializer,
    SupportRevisionSerializer,
    TentativeSerializer,
)
from .tasks import generer_support


class EstApprenantOrganisme(IsMembreOrganisme):
    message = "Seuls les apprenants passent les quiz."

    def has_permission(self, request, view):
        return (
            super().has_permission(request, view)
            and _role(request.user, view.kwargs["tenant_id"]) == MembreTenant.Role.APPRENANT
        )


def _formations_du_formateur(user, tenant_id):
    promotions = list(_promotions_du_formateur(user, tenant_id))
    return set(Promotion.objects.filter(id__in=promotions).values_list("formation_id", flat=True))


def _modules_de_l_apprenant(user, tenant_id):
    """Modules des briefs sur lesquels l'apprenant (ou son groupe) a déposé."""
    return set(
        Livrable.objects.filter(_assignations_de_l_apprenant(user, tenant_id, prefixe="assignation__"))
        .values_list("assignation__brief__module_id", flat=True)
    )


class SupportRevisionViewSet(viewsets.ModelViewSet):
    """
    Quiz et fiches de révision d'un module.

    - Formateur : voit ceux des modules des formations de ses promotions ;
      génère (POST) ; le créateur seul relit (PATCH, brouillon), publie,
      supprime.
    - Apprenant : supports publiés des modules où il a déposé ; passe les
      quiz (tentatives).
    - Admin organisme : lecture.

    Filtres : ?module=, ?type=.
    """

    serializer_class = SupportRevisionSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ("list", "retrieve", "tentatives"):
            if self.action == "tentatives" and self.request.method == "POST":
                return [EstApprenantOrganisme()]
            return [IsMembreOrganisme()]
        if self.action in ("create", "partial_update", "destroy", "publier"):
            return [IsFormateurOrganisme()]
        return _refus_par_defaut(self)

    def _role(self):
        if not hasattr(self, "_role_cache"):
            self._role_cache = _role(self.request.user, self.kwargs["tenant_id"])
        return self._role_cache

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs["tenant_id"]
        context["role"] = self._role()
        return context

    def get_queryset(self):
        tenant_id = self.kwargs["tenant_id"]
        user = self.request.user
        qs = (
            SupportRevision.objects.filter(module__formation__tenant_id=tenant_id)
            .select_related("module", "cree_par")
            .prefetch_related(
                Prefetch("questions", queryset=Question.objects.prefetch_related(
                    Prefetch("options", queryset=Option.objects.all())
                )),
                "ressources",
                "fichiers_livrables",
                "fichiers_ajoutes",
                "tentatives",
            )
        )
        module_id = _param_entier(self.request, "module")
        if module_id is not None:
            qs = qs.filter(module_id=module_id)
        type_ = self.request.query_params.get("type")
        if type_:
            qs = qs.filter(type=type_)

        role = self._role()
        if role == MembreTenant.Role.ADMINISTRATEUR:
            return qs
        if role == MembreTenant.Role.FORMATEUR:
            return qs.filter(module__formation_id__in=_formations_du_formateur(user, tenant_id))
        return qs.filter(
            statut=SupportRevision.Statut.PUBLIE,
            module_id__in=_modules_de_l_apprenant(user, tenant_id),
        )

    def _verifier_proprietaire(self, support):
        if support.cree_par_id != self.request.user.id:
            raise PermissionDenied("Seul le formateur qui l'a généré peut le modifier, le publier ou le supprimer.")

    def create(self, request, *args, **kwargs):
        tenant_id = self.kwargs["tenant_id"]
        serializer = GenerationSerializer(
            data=request.data,
            context={
                **self.get_serializer_context(),
                "formations_du_formateur": _formations_du_formateur(request.user, tenant_id),
                "promotions_du_formateur": list(_promotions_du_formateur(request.user, tenant_id)),
            },
        )
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            support = serializer.save()
            transaction.on_commit(lambda: generer_support.delay(support.pk))
        return Response(
            SupportRevisionSerializer(self.get_queryset().get(pk=support.pk), context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, *args, **kwargs):
        support = self.get_object()
        self._verifier_proprietaire(support)
        if support.statut != SupportRevision.Statut.BROUILLON:
            raise PermissionDenied("Seul un brouillon se corrige. Un support publié se supprime puis se régénère.")
        serializer = RelectureSerializer(support, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(self.get_serializer(self.get_queryset().get(pk=support.pk)).data)

    def perform_destroy(self, support):
        self._verifier_proprietaire(support)
        fichiers = [(f.fichier.storage, f.fichier.name) for f in support.fichiers_ajoutes.all()]
        support.delete()
        for stockage, nom in fichiers:
            stockage.delete(nom)

    @action(detail=True, methods=["post"])
    def publier(self, request, tenant_id=None, pk=None):
        support = self.get_object()
        self._verifier_proprietaire(support)
        if support.statut != SupportRevision.Statut.BROUILLON:
            raise PermissionDenied("Seul un brouillon relu peut être publié.")
        support.statut = SupportRevision.Statut.PUBLIE
        support.date_publication = timezone.now()
        support.save(update_fields=["statut", "date_publication", "date_modification"])
        return Response(self.get_serializer(support).data)

    @action(detail=True, methods=["get", "post"])
    def tentatives(self, request, tenant_id=None, pk=None):
        """
        GET : tentatives de l'apprenant connecté sur ce quiz (vide pour les autres rôles).
        POST : nouvelle tentative (apprenant, quiz publié) ; renvoie score et correction.
        """
        support = self.get_object()
        if not support.est_quiz:
            raise PermissionDenied("Une fiche de révision n'a pas de tentatives.")
        context = {**self.get_serializer_context(), "support": support}

        if request.method == "GET":
            tentatives = Tentative.objects.filter(support=support, apprenant=request.user)
            return Response(TentativeSerializer(tentatives, many=True, context=context).data)

        serializer = TentativeSerializer(data=request.data, context=context)
        serializer.is_valid(raise_exception=True)
        tentative = serializer.save()
        return Response(TentativeSerializer(tentative, context=context).data, status=status.HTTP_201_CREATED)
