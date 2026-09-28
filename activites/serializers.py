from rest_framework import serializers

from accounts.models import MembreTenant
from pedagogie.models import CompetenceNiveau, FormateurPromotion

from .models import (
    Assignation,
    Brief,
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
    class Meta:
        model = Ressource
        fields = [
            "id",
            "tenant",
            "formateur",
            "titre",
            "url",
            "fichier",
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

    def validate(self, attrs):
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

        if fichier and hasattr(fichier, "size") and fichier.size > 5 * 1024 * 1024:
            raise serializers.ValidationError({"fichier": "Le fichier ne doit pas dépasser 5 Mo."})

        return attrs


# ─── Brief ────────────────────────────────────────────────────────────────────

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
            "titre",
            "description",
            "consignes",
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


# ─── Livrable ─────────────────────────────────────────────────────────────────

class LivrableSerializer(serializers.ModelSerializer):
    class Meta:
        model = Livrable
        fields = [
            "id",
            "assignation",
            "deposant",
            "titre",
            "description",
            "statut",
            "date_depot",
            "date_modification",
        ]
        read_only_fields = [
            "id",
            "deposant",
            "statut",
            "date_depot",
            "date_modification",
        ]

    def validate(self, attrs):
        tenant_id = self.context["tenant_id"]
        request = self.context["request"]

        assignation = attrs.get("assignation", getattr(self.instance, "assignation", None))

        if assignation is None:
            raise serializers.ValidationError({"assignation": "L'assignation est obligatoire."})

        if str(assignation.brief.promotion.formation.tenant_id) != str(tenant_id):
            raise serializers.ValidationError({
                "assignation": "Cette assignation n'appartient pas à cet organisme."
            })

        utilisateur = request.user

        if not utilisateur.is_active:
            raise serializers.ValidationError({"assignation": "Votre compte est inactif."})

        # L'apprenant visé, ou un membre actif du groupe visé (une assignation
        # peut cibler les deux à la fois).
        est_cible = assignation.apprenant_id == utilisateur.id or (
            assignation.groupe_id
            and assignation.groupe.membres.filter(apprenant=utilisateur, actif=True).exists()
        )
        if not est_cible:
            raise serializers.ValidationError({
                "assignation": "Vous ne pouvez pas déposer un livrable pour cette assignation."
            })

        if not assignation.brief.promotion.inscriptions.filter(
            apprenant=utilisateur,
            actif=True,
        ).exists():
            raise serializers.ValidationError({
                "assignation": "Vous n'êtes plus inscrit activement à cette promotion."
            })

        if "titre" in attrs:
            titre = attrs["titre"].strip()
            if not titre:
                raise serializers.ValidationError({"titre": "Le titre ne peut pas être vide."})
            attrs["titre"] = titre

        if self.instance is not None and not self.context.get("statut_update", False):
            raise serializers.ValidationError(
                "Un livrable ne peut être modifié que par la mise à jour de son statut."
            )

        return attrs


class LivrableStatutSerializer(serializers.ModelSerializer):
    class Meta:
        model = Livrable
        fields = ["statut"]


# ─── FichierLivrable ──────────────────────────────────────────────────────────

class FichierLivrableSerializer(serializers.ModelSerializer):
    class Meta:
        model = FichierLivrable
        fields = [
            "id",
            "livrable",
            "nom",
            "fichier",
            "url",
            "date_creation",
        ]
        read_only_fields = [
            "id",
            "date_creation",
        ]

    def validate(self, attrs):
        tenant_id = self.context["tenant_id"]
        request = self.context["request"]

        livrable = attrs.get("livrable", getattr(self.instance, "livrable", None))

        if livrable is None:
            raise serializers.ValidationError({"livrable": "Le livrable est obligatoire."})

        if str(livrable.assignation.brief.promotion.formation.tenant_id) != str(tenant_id):
            raise serializers.ValidationError({
                "livrable": "Ce livrable n'appartient pas à cet organisme."
            })

        if livrable.deposant_id != request.user.id:
            raise serializers.ValidationError({
                "livrable": "Vous ne pouvez pas ajouter un fichier à ce livrable."
            })

        fichier = attrs.get("fichier", getattr(self.instance, "fichier", None))
        url = attrs.get("url", getattr(self.instance, "url", None))

        if bool(fichier) == bool(url):
            raise serializers.ValidationError({
                "fichier": "Renseignez soit un fichier, soit une URL.",
                "url": "Renseignez soit une URL, soit un fichier.",
            })

        if "nom" in attrs:
            nom = attrs["nom"].strip()
            if not nom:
                raise serializers.ValidationError({"nom": "Le nom ne peut pas être vide."})
            attrs["nom"] = nom

        if fichier and hasattr(fichier, "size") and fichier.size > 5 * 1024 * 1024:
            raise serializers.ValidationError({"fichier": "Le fichier ne doit pas dépasser 5 Mo."})

        return attrs
