from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import MembreTenant
from activites.permissions import IsMembreOrganisme
from activites.views import _role
from pedagogie.models import InscriptionPromotion, Promotion
from pedagogie.permissions import IsAdminOrganisme, _est_formateur_de_promotion
from pedagogie.views import _param_entier, _promotions_du_formateur

from . import exports
from .donnees import donnees_apprenant, donnees_promotion
from .models import Rapport
from .pdf import nom_du_fichier, pdf_du_rapport
from .serializers import RapportDetailSerializer, RapportSerializer
from .tasks import generer_rapport

LONGUEUR_MAX_CONTENU = 30000


def _verifier_accompagnant(user, tenant_id, promotion_id):
    """Admin de l'organisme, ou formateur de la promotion."""
    role = _role(user, tenant_id)
    if role == MembreTenant.Role.ADMINISTRATEUR:
        return
    if role == MembreTenant.Role.FORMATEUR and _est_formateur_de_promotion(user, promotion_id):
        return
    raise PermissionDenied("Réservé aux formateurs de la promotion et à l'administrateur.")


def _fichier(contenu, type_mime, nom):
    reponse = HttpResponse(contenu, content_type=type_mime)
    reponse["Content-Disposition"] = f'attachment; filename="{nom}"'
    return reponse


class RapportViewSet(viewsets.ModelViewSet):
    """
    Rapports de promotion et bilans individuels (models.py).

    - Formateur de la promotion, ou admin : demande (POST {type, promotion,
      apprenant?}), relit et modifie le brouillon (PATCH {contenu}), valide,
      partage un bilan validé avec l'apprenant, relance un rapport en échec.
    - Apprenant : lit seulement ses bilans validés et partagés.
    - pdf/ : export PDF (brouillon marqué comme tel).

    Filtres : ?type=, ?promotion=, ?apprenant=.
    """

    permission_classes = [IsMembreOrganisme]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_serializer_class(self):
        return RapportSerializer if self.action == "list" else RapportDetailSerializer

    def _role(self):
        return _role(self.request.user, self.kwargs["tenant_id"])

    def get_queryset(self):
        tenant_id = self.kwargs["tenant_id"]
        user = self.request.user
        qs = Rapport.objects.filter(promotion__formation__tenant_id=tenant_id).select_related(
            "promotion__formation", "apprenant", "auteur", "valide_par"
        )
        for param in ("promotion", "apprenant"):
            valeur = _param_entier(self.request, param)
            if valeur is not None:
                qs = qs.filter(**{f"{param}_id": valeur})
        type_ = self.request.query_params.get("type")
        if type_:
            qs = qs.filter(type=type_)
        if self.action == "list":
            qs = qs.defer("donnees")

        role = self._role()
        if role == MembreTenant.Role.ADMINISTRATEUR:
            return qs
        if role == MembreTenant.Role.FORMATEUR:
            return qs.filter(promotion_id__in=_promotions_du_formateur(user, tenant_id))
        return qs.filter(type=Rapport.Type.APPRENANT, apprenant=user, statut=Rapport.Statut.VALIDE, partage=True)

    def _rapport_modifiable(self):
        rapport = self.get_object()
        _verifier_accompagnant(self.request.user, self.kwargs["tenant_id"], rapport.promotion_id)
        return rapport

    def _reponse(self, rapport, code=status.HTTP_200_OK):
        rapport = self.get_queryset().get(pk=rapport.pk)
        return Response(RapportDetailSerializer(rapport).data, status=code)

    def create(self, request, tenant_id=None):
        type_ = request.data.get("type")
        if type_ not in Rapport.Type.values:
            raise ValidationError({"type": "Type de rapport inconnu."})
        try:
            promotion_id = int(request.data.get("promotion"))
        except (TypeError, ValueError):
            raise ValidationError({"promotion": "Précisez la promotion."})
        _verifier_accompagnant(request.user, tenant_id, promotion_id)
        promotion = get_object_or_404(
            Promotion.objects.select_related("formation__tenant"), pk=promotion_id, formation__tenant_id=tenant_id
        )

        apprenant = None
        if type_ == Rapport.Type.APPRENANT:
            try:
                apprenant_id = int(request.data.get("apprenant"))
            except (TypeError, ValueError):
                raise ValidationError({"apprenant": "Précisez l'apprenant."})
            inscription = (
                InscriptionPromotion.objects.filter(
                    Q(actif=True) | Q(fermee_par_cloture=True),
                    promotion=promotion,
                    apprenant_id=apprenant_id,
                )
                .select_related("apprenant")
                .first()
            )
            if inscription is None:
                raise NotFound("Apprenant introuvable dans cette promotion.")
            apprenant = inscription.apprenant

        if Rapport.objects.filter(
            type=type_, promotion=promotion, apprenant=apprenant, statut=Rapport.Statut.EN_COURS
        ).exists():
            raise ValidationError({"detail": "Un rapport est déjà en cours de génération."})

        donnees = (
            donnees_apprenant(apprenant, promotion) if apprenant else donnees_promotion(promotion)
        )
        rapport = Rapport.objects.create(
            type=type_, promotion=promotion, apprenant=apprenant, auteur=request.user, donnees=donnees
        )
        transaction.on_commit(lambda: generer_rapport.delay(rapport.pk))
        return self._reponse(rapport, status.HTTP_201_CREATED)

    def partial_update(self, request, tenant_id=None, pk=None):
        rapport = self._rapport_modifiable()
        if rapport.statut != Rapport.Statut.BROUILLON:
            raise ValidationError({"detail": "Seul un brouillon peut être modifié."})
        contenu = request.data.get("contenu")
        if not isinstance(contenu, str):
            raise ValidationError({"contenu": "Texte attendu."})
        if len(contenu) > LONGUEUR_MAX_CONTENU:
            raise ValidationError({"contenu": f"{LONGUEUR_MAX_CONTENU} caractères au plus."})
        rapport.contenu = contenu
        rapport.save(update_fields=["contenu", "date_modification"])
        return self._reponse(rapport)

    def update(self, request, *args, **kwargs):
        raise PermissionDenied("Modifiez seulement le texte du brouillon.")

    def destroy(self, request, tenant_id=None, pk=None):
        rapport = self._rapport_modifiable()
        if rapport.statut == Rapport.Statut.VALIDE and self._role() != MembreTenant.Role.ADMINISTRATEUR:
            raise PermissionDenied("Seul l'administrateur peut supprimer un rapport validé.")
        rapport.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def valider(self, request, tenant_id=None, pk=None):
        rapport = self._rapport_modifiable()
        if rapport.statut != Rapport.Statut.BROUILLON:
            raise ValidationError({"detail": "Seul un brouillon peut être validé."})
        if not rapport.contenu.strip():
            raise ValidationError({"detail": "Le rapport est vide."})
        rapport.statut = Rapport.Statut.VALIDE
        rapport.valide_par = request.user
        rapport.date_validation = timezone.now()
        rapport.save()
        return self._reponse(rapport)

    @action(detail=True, methods=["post"])
    def partager(self, request, tenant_id=None, pk=None):
        from notifications.models import Notification
        from notifications.services import notifier

        rapport = self._rapport_modifiable()
        if rapport.type != Rapport.Type.APPRENANT or rapport.statut != Rapport.Statut.VALIDE:
            raise ValidationError({"detail": "Seul un bilan individuel validé peut être partagé."})
        if not rapport.partage:
            rapport.partage = True
            rapport.save(update_fields=["partage", "date_modification"])
            notifier(
                [rapport.apprenant_id],
                Notification.Type.IA,
                "Votre bilan est disponible",
                f"{rapport.promotion.nom} : votre formateur a partagé votre bilan.",
                f"/rapports/{rapport.id}",
                tenant=tenant_id,
            )
        return self._reponse(rapport)

    @action(detail=True, methods=["post"])
    def relancer(self, request, tenant_id=None, pk=None):
        rapport = self._rapport_modifiable()
        if rapport.statut != Rapport.Statut.ECHEC:
            raise ValidationError({"detail": "Seul un rapport en échec peut être relancé."})
        rapport.statut = Rapport.Statut.EN_COURS
        rapport.erreur = ""
        rapport.save()
        transaction.on_commit(lambda: generer_rapport.delay(rapport.pk))
        return self._reponse(rapport)

    @action(detail=True, methods=["get"])
    def pdf(self, request, tenant_id=None, pk=None):
        rapport = self.get_object()
        if rapport.statut not in (Rapport.Statut.BROUILLON, Rapport.Statut.VALIDE):
            raise ValidationError({"detail": "Le rapport n'est pas encore prêt."})
        return _fichier(pdf_du_rapport(rapport), "application/pdf", nom_du_fichier(rapport))


# ─── Exports Excel ────────────────────────────────────────────────────────────

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class _ExportPromotionView(APIView):
    """Export d'une promotion : admin, ou formateur de la promotion."""

    permission_classes = [IsMembreOrganisme]
    exporter = None
    prefixe = ""

    def get(self, request, tenant_id, promotion_id):
        _verifier_accompagnant(request.user, tenant_id, promotion_id)
        promotion = get_object_or_404(
            Promotion.objects.select_related("formation__tenant"), pk=promotion_id, formation__tenant_id=tenant_id
        )
        contenu = type(self).exporter(promotion)
        return _fichier(contenu, XLSX, exports.nom_du_fichier(self.prefixe, promotion.nom))


class ExportProgressionView(_ExportPromotionView):
    exporter = exports.progression
    prefixe = "progression"


class ExportSuiviView(_ExportPromotionView):
    exporter = exports.suivi
    prefixe = "suivi-livrables"


class ExportMembresView(APIView):
    permission_classes = [IsAdminOrganisme]

    def get(self, request, tenant_id):
        from tenants.models import Tenant

        tenant = get_object_or_404(Tenant, pk=tenant_id)
        return _fichier(exports.membres(tenant), XLSX, exports.nom_du_fichier("membres", tenant.nom))
