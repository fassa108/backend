from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework import serializers

from accounts.models import MembreTenant
from pedagogie.models import CompetenceNiveau, FormateurPromotion, GroupeMembre

from .apercus import demander_apercu
from .texte_riche import est_vide, nettoyer_html
from .validators import valider_fichier
from .models import (
    EXTENSIONS_LIVRABLES,
    MAX_ELEMENTS_PAR_DEPOT,
    Assignation,
    Brief,
    CategorieBrief,
    FichierLivrable,
    Livrable,
    Ressource,
)


def _est_formateur_de_promotion(user, promotion_id):
    return FormateurPromotion.objects.filter(
        formateur=user,
        promotion_id=promotion_id,
    ).exists()


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
            "ressources",
            "cree_par",
            "cree_par_nom",
            "modifiable",
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

        ressources = attrs.get("ressources")
        if ressources is not None:
            if any(str(r.tenant_id) != str(tenant_id) for r in ressources):
                raise serializers.ValidationError({
                    "ressources": "Ces ressources n'appartiennent pas à cet organisme."
                })

        date_debut = attrs.get("date_debut", getattr(instance, "date_debut", None))
        date_limite = attrs.get("date_limite", getattr(instance, "date_limite", None))

        if date_debut and date_limite and date_limite < date_debut:
            raise serializers.ValidationError({
                "date_limite": "La date limite doit être postérieure ou égale à la date de début."
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
    """Un dépôt, en lecture (avec ses éléments et le retard calculé)."""

    brief = serializers.IntegerField(source="assignation.brief_id", read_only=True)
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

    def get_cible(self, livrable):
        a = livrable.assignation
        if a.groupe_id:
            return {"type": "groupe", "id": a.groupe_id, "nom": a.groupe.nom}
        return {
            "type": "apprenant",
            "id": a.apprenant_id,
            "nom": f"{a.apprenant.prenom} {a.apprenant.nom}",
        }


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
