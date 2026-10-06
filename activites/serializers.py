from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from accounts.models import MembreTenant
from pedagogie.models import CompetenceNiveau, FormateurPromotion, GroupeMembre

from .apercus import demander_apercu
from .texte_riche import est_vide, nettoyer_html
from .validators import valider_fichier
from .models import (
    EXTENSIONS_LIVRABLES,
    LONGUEUR_MAX_COMMENTAIRE,
    MAX_ELEMENTS_PAR_DEPOT,
    Assignation,
    Brief,
    CategorieBrief,
    CommentairePair,
    CompetenceValidee,
    Evaluation,
    EvaluationCompetence,
    FichierLivrable,
    Livrable,
    Ressource,
)


def _est_formateur_de_promotion(user, promotion_id):
    return FormateurPromotion.objects.filter(
        formateur=user,
        promotion_id=promotion_id,
    ).exists()


def peut_evaluer(user, brief):
    """
    Seul le créateur du brief évalue ; s'il n'est plus affecté à la
    promotion (ou n'existe plus), tout formateur de la promotion le peut.
    """
    formateurs = set(
        FormateurPromotion.objects.filter(promotion_id=brief.promotion_id)
        .values_list("formateur_id", flat=True)
    )
    if user.id not in formateurs:
        return False
    return brief.cree_par_id == user.id or brief.cree_par_id not in formateurs


def cible_assignation(a):
    """Qui est visé par l'assignation (apprenant ou groupe)."""
    if a.groupe_id:
        return {"type": "groupe", "id": a.groupe_id, "nom": a.groupe.nom}
    return {
        "type": "apprenant",
        "id": a.apprenant_id,
        "nom": f"{a.apprenant.prenom} {a.apprenant.nom}",
    }


def apprenants_de_l_assignation(a):
    """L'apprenant visé, ou les membres actifs du groupe visé."""
    if a.apprenant_id:
        return [a.apprenant]
    return [m.apprenant for m in a.groupe.membres.filter(actif=True).select_related("apprenant")]


def brief_a_des_livrables(brief):
    """Un brief est figé dès qu'un livrable a été déposé."""
    return Livrable.objects.filter(assignation__brief=brief).exists()


# ─── Ressource indépendante ───────────────────────────────────────────────────

class RessourceSerializer(serializers.ModelSerializer):
    # Le fichier s'envoie mais son chemin sur le disque n'est jamais renvoyé :
    # il se consulte ou se télécharge via les routes « consulter » et
    # « telecharger ». On expose seulement son type et son extension.
    type = serializers.SerializerMethodField()
    extension = serializers.SerializerMethodField()

    class Meta:
        model = Ressource
        fields = [
            "id",
            "tenant",
            "formateur",
            "titre",
            "type",
            "url",
            "fichier",
            "extension",
            "date_creation",
            "date_modification",
        ]
        read_only_fields = [
            "id",
            "tenant",
            "formateur",
            "date_creation",
            "date_modification",
        ]
        extra_kwargs = {"fichier": {"write_only": True}}

    def get_type(self, ressource):
        return "lien" if ressource.url else "fichier"

    def get_extension(self, ressource):
        if not ressource.fichier:
            return ""
        return ressource.fichier.name.rsplit(".", 1)[-1].lower()

    def validate(self, attrs):
        # Lien vide = pas de lien (la contrainte en base exige NULL quand
        # la ressource est un fichier, ex. passage d'un lien à un fichier).
        if "url" in attrs and not attrs["url"]:
            attrs["url"] = None

        url = attrs.get("url", getattr(self.instance, "url", None))
        fichier = attrs.get("fichier", getattr(self.instance, "fichier", None))

        if bool(url) == bool(fichier):
            raise serializers.ValidationError({
                "url": "Une seule source doit être renseignée : une URL ou un fichier.",
                "fichier": "Une seule source doit être renseignée : une URL ou un fichier.",
            })

        if "titre" in attrs:
            titre = attrs["titre"].strip()
            if not titre:
                raise serializers.ValidationError({"titre": "Le titre ne peut pas être vide."})
            attrs["titre"] = titre

        # Nouveau fichier envoyé : taille (10 Mo) et contenu réel
        if "fichier" in attrs and attrs["fichier"]:
            try:
                valider_fichier(attrs["fichier"])
            except serializers.ValidationError as erreur:
                raise serializers.ValidationError({"fichier": erreur.detail})

        return attrs


# ─── Catégories de brief ─────────────────────────────────────────────────────

class CategorieBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = CategorieBrief
        fields = ["id", "nom", "actif", "date_creation", "date_modification"]
        read_only_fields = ["id", "date_creation", "date_modification"]

    def validate_nom(self, value):
        nom = value.strip()
        if not nom:
            raise serializers.ValidationError("Le nom est obligatoire.")
        qs = CategorieBrief.objects.filter(tenant_id=self.context["tenant_id"], nom__iexact=nom)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Une catégorie avec ce nom existe déjà.")
        return nom


# ─── Brief ────────────────────────────────────────────────────────────────────

# Sections du brief en texte riche ; celles de SECTIONS_OBLIGATOIRES ne
# peuvent pas être vides.
SECTIONS_TEXTE_RICHE = (
    "contexte",
    "modalites_pedagogiques",
    "modalites_evaluation",
    "criteres_performance",
    "livrables_attendus",
)
SECTIONS_OBLIGATOIRES = {
    "modalites_evaluation": "Les modalités d'évaluation sont obligatoires.",
    "livrables_attendus": "Les livrables attendus sont obligatoires.",
}


class BriefSerializer(serializers.ModelSerializer):
    competence_niveaux = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=CompetenceNiveau.objects.select_related("competence__module"),
        required=False,
    )
    ressources = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Ressource.objects.all(),
        required=False,
    )
    cree_par_nom = serializers.SerializerMethodField()
    # False dès le premier livrable : le brief est alors figé
    # (seul son statut peut encore changer).
    modifiable = serializers.SerializerMethodField()
    # L'utilisateur connecté peut-il évaluer les rendus de ce brief ?
    peut_evaluer = serializers.SerializerMethodField()
    # Compétences visées avec leurs libellés (lecture)
    competences_visees = serializers.SerializerMethodField()

    class Meta:
        model = Brief
        fields = [
            "id",
            "promotion",
            "module",
            "categorie",
            "titre",
            "description",
            "contexte",
            "modalites_pedagogiques",
            "modalites_evaluation",
            "criteres_performance",
            "livrables_attendus",
            "date_debut",
            "date_limite",
            "statut",
            "competence_niveaux",
            "competences_visees",
            "ressources",
            "cree_par",
            "cree_par_nom",
            "modifiable",
            "peut_evaluer",
            "date_creation",
            "date_modification",
        ]
        read_only_fields = [
            "id",
            "cree_par",
            "date_creation",
            "date_modification",
        ]

    def get_cree_par_nom(self, brief):
        if not brief.cree_par:
            return None
        return f"{brief.cree_par.prenom} {brief.cree_par.nom}"

    def get_modifiable(self, brief):
        return not brief_a_des_livrables(brief)

    def get_competences_visees(self, brief):
        return [
            {
                "id": cn.id,
                "competence": cn.competence.nom,
                "niveau": cn.niveau.nom,
                "description": cn.description,
            }
            for cn in brief.competence_niveaux.all()
        ]

    def get_peut_evaluer(self, brief):
        request = self.context.get("request")
        return bool(request and peut_evaluer(request.user, brief))

    def validate(self, attrs):
        tenant_id = self.context["tenant_id"]
        request = self.context["request"]
        instance = self.instance

        # Brief figé après le premier livrable : seul le statut change encore.
        if instance and brief_a_des_livrables(instance):
            autres = set(attrs) - {"statut"}
            if autres:
                raise serializers.ValidationError(
                    "Ce brief a déjà des livrables : il ne peut plus être modifié "
                    "(seul son statut peut changer)."
                )

        if instance and "promotion" in attrs and attrs["promotion"].pk != instance.promotion_id:
            raise serializers.ValidationError(
                {"promotion": "Un brief ne peut pas changer de promotion."}
            )

        promotion = attrs.get("promotion", getattr(instance, "promotion", None))
        if promotion is None:
            raise serializers.ValidationError({"promotion": "La promotion est obligatoire."})

        if str(promotion.formation.tenant_id) != str(tenant_id):
            raise serializers.ValidationError({
                "promotion": "Cette promotion n'appartient pas à cet organisme."
            })

        if not _est_formateur_de_promotion(request.user, promotion.id):
            raise serializers.ValidationError({
                "promotion": "Vous n'êtes pas affecté à cette promotion."
            })

        if not promotion.actif:
            raise serializers.ValidationError({
                "promotion": "Cette promotion est clôturée : elle est en lecture seule."
            })

        # Catégorie facultative : de l'organisme, active (sauf si déjà en place)
        if "categorie" in attrs and attrs["categorie"] is not None:
            categorie = attrs["categorie"]
            if str(categorie.tenant_id) != str(tenant_id):
                raise serializers.ValidationError({
                    "categorie": "Cette catégorie n'appartient pas à cet organisme."
                })
            if not categorie.actif and getattr(instance, "categorie_id", None) != categorie.id:
                raise serializers.ValidationError({"categorie": "Cette catégorie est désactivée."})

        # Module principal : obligatoire, dans la formation de la promotion
        module = attrs.get("module", getattr(instance, "module", None))
        if module is None:
            raise serializers.ValidationError({"module": "Le module est obligatoire."})
        if module.formation_id != promotion.formation_id:
            raise serializers.ValidationError({
                "module": "Le module doit appartenir à la formation de la promotion."
            })

        # Compétences visées : de n'importe quel module de la formation
        competence_niveaux = attrs.get("competence_niveaux")
        if competence_niveaux is not None:
            if any(
                cn.competence.module.formation_id != promotion.formation_id
                for cn in competence_niveaux
            ):
                raise serializers.ValidationError({
                    "competence_niveaux": (
                        "Les compétences visées doivent appartenir à la formation "
                        "de la promotion."
                    )
                })

            # Une compétence-niveau n'est visée que par un seul brief de la promotion
            deja_visees = (
                Brief.competence_niveaux.through.objects.filter(
                    brief__promotion=promotion,
                    competenceniveau__in=competence_niveaux,
                )
                .exclude(brief_id=getattr(instance, "pk", None))
                .select_related("brief", "competenceniveau__competence", "competenceniveau__niveau")
            )
            if deja_visees:
                noms = ", ".join(
                    f"{d.competenceniveau.competence.nom} ({d.competenceniveau.niveau.nom}) "
                    f"par « {d.brief.titre} »"
                    for d in deja_visees
                )
                raise serializers.ValidationError({
                    "competence_niveaux": f"Déjà visée dans cette promotion : {noms}."
                })

        ressources = attrs.get("ressources")
        if ressources is not None:
            if any(str(r.tenant_id) != str(tenant_id) for r in ressources):
                raise serializers.ValidationError({
                    "ressources": "Ces ressources n'appartiennent pas à cet organisme."
                })

        date_debut = attrs.get("date_debut", getattr(instance, "date_debut", None))
        date_limite = attrs.get("date_limite", getattr(instance, "date_limite", None))

        if date_debut and date_limite and date_limite <= date_debut:
            raise serializers.ValidationError({
                "date_limite": "La date limite doit être postérieure à la date de début."
            })

        # Pas de brief qui commence dans le passé (au jour près). En modification,
        # seulement si la date change : un brief déjà commencé reste modifiable.
        if (
            "date_debut" in attrs and date_debut
            and timezone.localdate(date_debut) < timezone.localdate()
            and not (instance and timezone.localdate(date_debut) == timezone.localdate(instance.date_debut))
        ):
            raise serializers.ValidationError({
                "date_debut": "La date de début ne peut pas être avant aujourd'hui."
            })

        # Le brief se déroule pendant la promotion (vérifié quand les dates changent).
        if "date_debut" in attrs and date_debut and timezone.localdate(date_debut) < promotion.date_debut:
            raise serializers.ValidationError({
                "date_debut": (
                    "Le brief ne peut pas commencer avant le début de la promotion "
                    f"({promotion.date_debut:%d/%m/%Y})."
                )
            })
        if (
            "date_limite" in attrs and date_limite and promotion.date_fin
            and timezone.localdate(date_limite) > promotion.date_fin
        ):
            raise serializers.ValidationError({
                "date_limite": (
                    "La date limite ne peut pas dépasser la fin de la promotion "
                    f"({promotion.date_fin:%d/%m/%Y})."
                )
            })

        # Statut : un brief déjà rendu ne redevient pas brouillon (il disparaîtrait
        # pour les apprenants, avec leurs livrables) ; on ne publie pas un brief échu.
        statut = attrs.get("statut", getattr(instance, "statut", Brief.Statut.BROUILLON))
        ancien_statut = getattr(instance, "statut", None)
        if (
            instance and statut == Brief.Statut.BROUILLON and ancien_statut != Brief.Statut.BROUILLON
            and brief_a_des_livrables(instance)
        ):
            raise serializers.ValidationError({
                "statut": "Des livrables ont été déposés : ce brief ne peut plus repasser en brouillon."
            })
        if (
            statut == Brief.Statut.PUBLIE
            and ancien_statut in (None, Brief.Statut.BROUILLON)
            and date_limite and date_limite <= timezone.now()
        ):
            raise serializers.ValidationError({
                "date_limite": "La date limite est déjà passée : choisissez une date future pour publier ce brief."
            })

        if "titre" in attrs:
            titre = attrs["titre"].strip()
            if not titre:
                raise serializers.ValidationError({"titre": "Le titre ne peut pas être vide."})
            attrs["titre"] = titre

        if "description" in attrs:
            attrs["description"] = attrs["description"].strip()
            if not attrs["description"]:
                raise serializers.ValidationError({"description": "La description est obligatoire."})

        # Texte riche : HTML nettoyé côté serveur, sections obligatoires non vides
        for champ in SECTIONS_TEXTE_RICHE:
            if champ in attrs:
                attrs[champ] = nettoyer_html(attrs[champ])

        erreurs = {}
        for champ, message in SECTIONS_OBLIGATOIRES.items():
            valeur = attrs.get(champ, getattr(instance, champ, ""))
            if est_vide(valeur):
                erreurs[champ] = message
        if erreurs:
            raise serializers.ValidationError(erreurs)

        return attrs


# ─── Assignation ──────────────────────────────────────────────────────────────

class AssignationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assignation
        fields = [
            "id",
            "brief",
            "groupe",
            "apprenant",
            "date_creation",
        ]
        read_only_fields = [
            "id",
            "date_creation",
        ]

    def validate(self, attrs):
        tenant_id = self.context["tenant_id"]
        request = self.context["request"]

        brief = attrs.get("brief")
        groupe = attrs.get("groupe")
        apprenant = attrs.get("apprenant")

        if brief is None:
            raise serializers.ValidationError({"brief": "Le brief est obligatoire."})

        if str(brief.promotion.formation.tenant_id) != str(tenant_id):
            raise serializers.ValidationError({"brief": "Ce brief n'appartient pas à cet organisme."})

        # Le formateur doit être affecté à la promotion du brief
        if not _est_formateur_de_promotion(request.user, brief.promotion_id):
            raise serializers.ValidationError({
                "brief": "Vous n'êtes pas affecté à la promotion de ce brief."
            })

        if not brief.promotion.actif:
            raise serializers.ValidationError({
                "brief": "La promotion de ce brief est clôturée : elle est en lecture seule."
            })

        if brief.statut == Brief.Statut.ARCHIVE:
            raise serializers.ValidationError({"brief": "Ce brief est archivé."})

        # Au moins une cible : apprenant seul, groupe seul, ou les deux.
        if not groupe and not apprenant:
            raise serializers.ValidationError({
                "groupe": "Au moins une cible doit être renseignée : un groupe, un apprenant, ou les deux.",
                "apprenant": "Au moins une cible doit être renseignée : un groupe, un apprenant, ou les deux.",
            })

        promotion = brief.promotion

        if groupe:
            if groupe.promotion_id != promotion.id:
                raise serializers.ValidationError({
                    "groupe": "Le groupe doit appartenir à la promotion du brief."
                })
            if not groupe.actif:
                raise serializers.ValidationError({"groupe": "Ce groupe est désactivé."})

        if apprenant:
            # NOTE : Utilisateur.actif n'est PAS vérifié.
            # Seule l'appartenance active au tenant (MembreTenant.actif) est vérifiée.
            membre = MembreTenant.objects.filter(
                utilisateur=apprenant,
                tenant_id=tenant_id,
                role=MembreTenant.Role.APPRENANT,
                actif=True,
            ).exists()

            if not membre:
                raise serializers.ValidationError({
                    "apprenant": "Cet utilisateur n'est pas un apprenant actif de cet organisme."
                })

            inscription = promotion.inscriptions.filter(
                apprenant=apprenant,
                actif=True,
            ).exists()

            if not inscription:
                raise serializers.ValidationError({
                    "apprenant": "Cet apprenant n'est pas inscrit activement à la promotion du brief."
                })

        # Un apprenant n'est assigné qu'une fois à un brief : pas d'assignation
        # individuelle s'il fait partie d'un groupe assigné, et inversement.
        if apprenant:
            groupes_assignes = Assignation.objects.filter(
                brief=brief,
                groupe__membres__apprenant=apprenant,
                groupe__membres__actif=True,
            ).values_list("groupe__nom", flat=True)
            if groupe and groupe.membres.filter(apprenant=apprenant, actif=True).exists():
                groupes_assignes = [groupe.nom, *groupes_assignes]
            if groupes_assignes:
                raise serializers.ValidationError({
                    "apprenant": (
                        "Cet apprenant fait déjà partie du groupe "
                        f"« {groupes_assignes[0]} » assigné à ce brief."
                    )
                })

        if groupe:
            # Membres déjà couverts par un autre groupe assigné au brief
            communs = GroupeMembre.objects.filter(
                groupe__assignations__brief=brief,
                actif=True,
                apprenant__in=groupe.membres.filter(actif=True).values("apprenant"),
            ).exclude(groupe=groupe).select_related("apprenant", "groupe")
            if communs.exists():
                noms = ", ".join(
                    f"{m.apprenant.prenom} {m.apprenant.nom} ({m.groupe.nom})" for m in communs
                )
                raise serializers.ValidationError({
                    "groupe": (
                        "Des membres de ce groupe sont déjà assignés via un autre groupe : "
                        f"{noms}."
                    )
                })

            deja_assignes = Assignation.objects.filter(
                brief=brief,
                apprenant__appartenances_groupes__groupe=groupe,
                apprenant__appartenances_groupes__actif=True,
            ).select_related("apprenant")
            if deja_assignes.exists():
                noms = ", ".join(
                    f"{a.apprenant.prenom} {a.apprenant.nom}" for a in deja_assignes
                )
                raise serializers.ValidationError({
                    "groupe": (
                        "Des membres de ce groupe sont déjà assignés individuellement "
                        f"à ce brief : {noms}. Retirez d'abord leur assignation."
                    )
                })

        # Anti-doublon
        if groupe and Assignation.objects.filter(brief=brief, groupe=groupe).exists():
            raise serializers.ValidationError({
                "groupe": "Ce groupe est déjà assigné à ce brief."
            })
        if apprenant and Assignation.objects.filter(brief=brief, apprenant=apprenant).exists():
            raise serializers.ValidationError({
                "apprenant": "Cet apprenant est déjà assigné à ce brief."
            })

        return attrs


# ─── Livrables (dépôts) ───────────────────────────────────────────────────────

class FichierLivrableSerializer(serializers.ModelSerializer):
    """
    Élément d'un dépôt, en lecture. Le chemin du fichier sur le disque n'est
    jamais exposé : un fichier se consulte ou se télécharge via les routes
    « consulter » et « telecharger », qui vérifient les droits.
    """

    type = serializers.SerializerMethodField()
    extension = serializers.CharField(read_only=True)

    class Meta:
        model = FichierLivrable
        fields = ["id", "livrable", "nom", "type", "extension", "url", "date_creation"]
        read_only_fields = fields

    def get_type(self, element):
        return "lien" if element.url else "fichier"


class LivrableSerializer(serializers.ModelSerializer):
    """
    Un dépôt, en lecture (avec ses éléments et le retard calculé).
    Son message n'est pas renvoyé aux pairs.
    """

    brief = serializers.IntegerField(source="assignation.brief_id", read_only=True)
    brief_titre = serializers.CharField(source="assignation.brief.titre", read_only=True)
    deposant_nom = serializers.SerializerMethodField()
    # Qui est visé par l'assignation (apprenant ou groupe)
    cible = serializers.SerializerMethodField()
    en_retard = serializers.BooleanField(read_only=True)
    fichiers = FichierLivrableSerializer(many=True, read_only=True)

    class Meta:
        model = Livrable
        fields = [
            "id",
            "assignation",
            "brief",
            "brief_titre",
            "cible",
            "numero",
            "deposant",
            "deposant_nom",
            "commentaire",
            "date_depot",
            "en_retard",
            "fichiers",
        ]
        read_only_fields = fields

    def get_deposant_nom(self, livrable):
        return f"{livrable.deposant.prenom} {livrable.deposant.nom}"

    def to_representation(self, livrable):
        data = super().to_representation(livrable)
        # Le message d'un dépôt est réservé à ses auteurs et aux formateurs :
        # un apprenant ne voit pas celui de ses pairs.
        request = self.context.get("request")
        if (
            request
            and self.context.get("role") == MembreTenant.Role.APPRENANT
            and not est_vise_par(request.user, livrable.assignation)
        ):
            data["commentaire"] = ""
        return data

    def get_cible(self, livrable):
        return cible_assignation(livrable.assignation)


class DepotSerializer(serializers.Serializer):
    """
    Création d'un dépôt en une fois (multipart) :
    assignation, commentaire (facultatif), fichiers[] et liens[].
    Au moins un élément, au plus MAX_ELEMENTS_PAR_DEPOT.
    """

    assignation = serializers.PrimaryKeyRelatedField(
        queryset=Assignation.objects.select_related("brief__promotion", "groupe", "apprenant"),
    )
    commentaire = serializers.CharField(required=False, allow_blank=True, default="")
    fichiers = serializers.ListField(child=serializers.FileField(), required=False, default=list)
    liens = serializers.ListField(child=serializers.URLField(), required=False, default=list)

    def validate(self, attrs):
        tenant_id = self.context["tenant_id"]
        utilisateur = self.context["request"].user
        assignation = attrs["assignation"]
        brief = assignation.brief

        if str(brief.promotion.formation.tenant_id) != str(tenant_id):
            raise serializers.ValidationError({
                "assignation": "Cette assignation n'appartient pas à cet organisme."
            })

        # L'apprenant visé, ou un membre actif du groupe visé (une assignation
        # peut cibler les deux à la fois).
        est_cible = assignation.apprenant_id == utilisateur.id or (
            assignation.groupe_id
            and assignation.groupe.membres.filter(apprenant=utilisateur, actif=True).exists()
        )
        if not est_cible:
            raise serializers.ValidationError({
                "assignation": "Vous ne pouvez pas déposer pour cette assignation."
            })

        if not brief.promotion.inscriptions.filter(apprenant=utilisateur, actif=True).exists():
            raise serializers.ValidationError({
                "assignation": "Vous n'êtes plus inscrit activement à cette promotion."
            })

        if not brief.promotion.actif:
            raise serializers.ValidationError({
                "assignation": "La promotion est clôturée : les dépôts sont fermés."
            })

        if brief.statut != Brief.Statut.PUBLIE:
            raise serializers.ValidationError({
                "assignation": "Ce brief n'est pas ouvert aux dépôts."
            })

        if timezone.now() < brief.date_debut:
            debut = timezone.localtime(brief.date_debut).strftime("%d/%m/%Y à %H:%M")
            raise serializers.ValidationError({
                "assignation": f"Les dépôts ouvrent le {debut}."
            })
        # Après la date limite : accepté, le retard est calculé (en_retard).

        fichiers, liens = attrs["fichiers"], attrs["liens"]
        total = len(fichiers) + len(liens)
        if total == 0:
            raise serializers.ValidationError("Ajoutez au moins un fichier ou un lien.")
        if total > MAX_ELEMENTS_PAR_DEPOT:
            raise serializers.ValidationError(
                f"Un dépôt contient au plus {MAX_ELEMENTS_PAR_DEPOT} fichiers et liens."
            )

        erreurs_fichiers = []
        for fichier in fichiers:
            ext = fichier.name.rsplit(".", 1)[-1].lower() if "." in fichier.name else ""
            try:
                if ext not in EXTENSIONS_LIVRABLES:
                    raise serializers.ValidationError(
                        "Format non accepté (pdf, docx, pptx ou txt)."
                    )
                valider_fichier(fichier)
            except serializers.ValidationError as erreur:
                erreurs_fichiers.append(f"{fichier.name} : {erreur.detail[0]}")
        if erreurs_fichiers:
            raise serializers.ValidationError({"fichiers": erreurs_fichiers})

        attrs["commentaire"] = attrs["commentaire"].strip()
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        assignation = validated_data["assignation"]
        # Verrou sur l'assignation : deux dépôts simultanés n'obtiennent pas
        # le même numéro.
        Assignation.objects.select_for_update().get(pk=assignation.pk)
        dernier = assignation.livrables.aggregate(m=Max("numero"))["m"] or 0

        livrable = Livrable.objects.create(
            assignation=assignation,
            deposant=self.context["request"].user,
            numero=dernier + 1,
            commentaire=validated_data["commentaire"],
        )
        for fichier in validated_data["fichiers"]:
            element = FichierLivrable.objects.create(livrable=livrable, nom=fichier.name[:255], fichier=fichier)
            demander_apercu(element)
        for url in validated_data["liens"]:
            FichierLivrable.objects.create(livrable=livrable, nom=url[:255], url=url)
        return livrable


# ─── Évaluations ──────────────────────────────────────────────────────────────

class EvaluationCompetenceSerializer(serializers.ModelSerializer):
    competence_nom = serializers.CharField(source="competence_niveau.competence.nom", read_only=True)
    niveau_nom = serializers.CharField(source="competence_niveau.niveau.nom", read_only=True)

    class Meta:
        model = EvaluationCompetence
        fields = ["competence_niveau", "competence_nom", "niveau_nom", "acquis"]


class EvaluationSerializer(serializers.ModelSerializer):
    """
    Évaluation d'un rendu. Création : assignation, commentaire général, et
    une ligne par compétence-niveau visée par le brief (toutes, chacune une
    fois) : acquise ou non.

    Règles :
    - évaluateur : créateur du brief, ou un formateur de la promotion si le
      créateur n'y est plus affecté ;
    - brief publié ou archivé, promotion ouverte ;
    - sans dépôt, rien ne peut être acquis ;
    - une compétence acquise lors de la précédente évaluation de ce rendu ne
      peut pas redevenir non acquise (une validation est définitive).
    """

    competences = EvaluationCompetenceSerializer(many=True, required=False)
    brief = serializers.IntegerField(source="assignation.brief_id", read_only=True)
    cible = serializers.SerializerMethodField()
    evaluateur_nom = serializers.SerializerMethodField()

    class Meta:
        model = Evaluation
        fields = [
            "id",
            "assignation",
            "brief",
            "cible",
            "evaluateur",
            "evaluateur_nom",
            "commentaire",
            "competences",
            "date_creation",
        ]
        read_only_fields = ["id", "evaluateur", "date_creation"]

    def get_cible(self, evaluation):
        return cible_assignation(evaluation.assignation)

    def get_evaluateur_nom(self, evaluation):
        e = evaluation.evaluateur
        return f"{e.prenom} {e.nom}" if e else None

    def validate(self, attrs):
        user = self.context["request"].user
        tenant_id = self.context["tenant_id"]
        assignation = attrs["assignation"]
        brief = assignation.brief

        if brief.promotion.formation.tenant_id != tenant_id:
            raise serializers.ValidationError({"assignation": "Assignation introuvable."})
        if not peut_evaluer(user, brief):
            raise PermissionDenied(
                "Seul le formateur qui a créé ce brief peut évaluer ses rendus "
                "(ou un formateur de la promotion s'il n'y est plus affecté)."
            )
        if brief.statut == Brief.Statut.BROUILLON:
            raise serializers.ValidationError({"assignation": "Un brief en brouillon ne s'évalue pas."})
        if not brief.promotion.actif:
            raise serializers.ValidationError(
                {"assignation": "La promotion est clôturée : ses rendus ne s'évaluent plus."}
            )

        lignes = attrs.get("competences", [])
        visees = set(brief.competence_niveaux.values_list("id", flat=True))
        fournies = [l["competence_niveau"].id for l in lignes]
        if len(fournies) != len(set(fournies)) or set(fournies) != visees:
            raise serializers.ValidationError(
                {"competences": "Évaluez chacune des compétences visées par le brief, une seule fois."}
            )

        if not assignation.livrables.exists() and any(l["acquis"] for l in lignes):
            raise serializers.ValidationError(
                {"competences": "Aucun dépôt pour ce rendu : les compétences ne peuvent pas être acquises."}
            )

        precedente = assignation.evaluations.first()
        if precedente:
            deja_acquises = set(
                precedente.competences.filter(acquis=True).values_list("competence_niveau_id", flat=True)
            )
            retirees = [l for l in lignes if not l["acquis"] and l["competence_niveau"].id in deja_acquises]
            if retirees:
                noms = ", ".join(
                    f"{l['competence_niveau'].competence.nom} ({l['competence_niveau'].niveau.nom})"
                    for l in retirees
                )
                raise serializers.ValidationError(
                    {"competences": f"Déjà acquis lors de la précédente évaluation, c'est définitif : {noms}."}
                )

        attrs["commentaire"] = attrs.get("commentaire", "").strip()
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        lignes = validated_data.pop("competences", [])
        evaluation = Evaluation.objects.create(**validated_data)
        EvaluationCompetence.objects.bulk_create(
            EvaluationCompetence(evaluation=evaluation, **l) for l in lignes
        )
        # Compétences acquises : validées pour chaque apprenant concerné
        acquises = [l["competence_niveau"] for l in lignes if l["acquis"]]
        for apprenant in apprenants_de_l_assignation(evaluation.assignation):
            for cn in acquises:
                CompetenceValidee.objects.get_or_create(
                    apprenant=apprenant, competence_niveau=cn, defaults={"evaluation": evaluation}
                )
        return evaluation


# ─── Feedback entre pairs ─────────────────────────────────────────────────────

def est_vise_par(user, assignation):
    """L'utilisateur est l'apprenant visé, ou un membre actif du groupe visé."""
    if assignation.apprenant_id:
        return assignation.apprenant_id == user.id
    return assignation.groupe.membres.filter(apprenant=user, actif=True).exists()


def a_depose_sur(user, brief):
    """L'apprenant (ou l'un de ses groupes) a déposé sur ce brief."""
    return Livrable.objects.filter(assignation__brief=brief).filter(
        Q(assignation__apprenant=user)
        | Q(assignation__groupe__membres__apprenant=user, assignation__groupe__membres__actif=True)
    ).exists()


def brief_ouvert_aux_commentaires(brief):
    return brief.statut == Brief.Statut.PUBLIE and brief.promotion.actif


class CommentairePairSerializer(serializers.ModelSerializer):
    """
    Commentaire sur le rendu d'un pair, ou réponse (un niveau).
    Création : assignation (rendu commenté), texte, parent (réponse).
    Modification : le texte seulement.
    """

    brief = serializers.IntegerField(source="assignation.brief_id", read_only=True)
    auteur_nom = serializers.SerializerMethodField()
    modifie = serializers.SerializerMethodField()
    peut_modifier = serializers.SerializerMethodField()

    class Meta:
        model = CommentairePair
        fields = [
            "id",
            "assignation",
            "brief",
            "parent",
            "auteur",
            "auteur_nom",
            "texte",
            "masque",
            "modifie",
            "peut_modifier",
            "date_creation",
            "date_modification",
        ]
        read_only_fields = ["id", "auteur", "masque", "date_creation", "date_modification"]

    def get_auteur_nom(self, c):
        return f"{c.auteur.prenom} {c.auteur.nom}"

    def get_modifie(self, c):
        return (c.date_modification - c.date_creation).total_seconds() > 1

    def get_peut_modifier(self, c):
        request = self.context.get("request")
        return bool(
            request
            and c.auteur_id == request.user.id
            and not c.masque
            and brief_ouvert_aux_commentaires(c.assignation.brief)
        )

    def validate_texte(self, texte):
        texte = texte.strip()
        if not texte:
            raise serializers.ValidationError("Le commentaire est vide.")
        if len(texte) > LONGUEUR_MAX_COMMENTAIRE:
            raise serializers.ValidationError(
                f"{LONGUEUR_MAX_COMMENTAIRE} caractères au maximum."
            )
        return texte

    def validate(self, attrs):
        user = self.context["request"].user

        if self.instance is not None:
            # Modification : seul le texte change
            attrs = {"texte": attrs["texte"]} if "texte" in attrs else {}
            if not brief_ouvert_aux_commentaires(self.instance.assignation.brief):
                raise serializers.ValidationError("Ce brief est fermé : les commentaires sont en lecture seule.")
            if self.instance.masque:
                raise serializers.ValidationError("Un commentaire masqué par le formateur ne se modifie plus.")
            return attrs

        assignation = attrs["assignation"]
        brief = assignation.brief
        if brief.promotion.formation.tenant_id != self.context["tenant_id"]:
            raise serializers.ValidationError({"assignation": "Rendu introuvable."})
        if not brief_ouvert_aux_commentaires(brief):
            raise serializers.ValidationError("Ce brief est fermé : les commentaires sont en lecture seule.")
        if not a_depose_sur(user, brief):
            raise serializers.ValidationError(
                "Déposez d'abord votre travail pour commenter celui des autres."
            )
        if not assignation.livrables.exists():
            raise serializers.ValidationError({"assignation": "Ce rendu n'a pas encore de dépôt."})

        parent = attrs.get("parent")
        if parent is None:
            if est_vise_par(user, assignation):
                raise serializers.ValidationError(
                    "Vous ne commentez pas votre propre rendu : répondez aux commentaires reçus."
                )
        else:
            if parent.assignation_id != assignation.id:
                raise serializers.ValidationError({"parent": "Ce commentaire porte sur un autre rendu."})
            if parent.parent_id is not None:
                raise serializers.ValidationError({"parent": "On répond à un commentaire, pas à une réponse."})
            if parent.masque:
                raise serializers.ValidationError({"parent": "Ce commentaire a été masqué."})
        return attrs
