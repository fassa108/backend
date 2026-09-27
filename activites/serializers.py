from rest_framework import serializers

from accounts.models import MembreTenant
from pedagogie.models import Competence, FormateurPromotion

from .models import (
    Assignation,
    Brief,
    FichierLivrable,
    Livrable,
    Ressource,
    RessourceBrief,
)


def _est_formateur_de_promotion(user, promotion_id):
    return FormateurPromotion.objects.filter(
        formateur=user,
        promotion_id=promotion_id,
    ).exists()


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
    competences = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Competence.objects.all(),
        required=False,
    )

    class Meta:
        model = Brief
        fields = [
            "id",
            "promotion",
            "titre",
            "description",
            "consignes",
            "date_debut",
            "date_limite",
            "statut",
            "competences",
            "date_creation",
            "date_modification",
        ]
        read_only_fields = [
            "id",
            "date_creation",
            "date_modification",
        ]

    def validate(self, attrs):
        tenant_id = self.context["tenant_id"]
        request = self.context.get("request")

        promotion = attrs.get(
            "promotion",
            getattr(self.instance, "promotion", None),
        )

        if promotion is None:
            raise serializers.ValidationError({"promotion": "La promotion est obligatoire."})

        if str(promotion.formation.tenant_id) != str(tenant_id):
            raise serializers.ValidationError({
                "promotion": "Cette promotion n'appartient pas à cet organisme."
            })

        # Vérification que le formateur est affecté à la promotion
        if request and request.user and not request.user.est_admin_saas:
            is_admin = MembreTenant.objects.filter(
                utilisateur=request.user,
                tenant_id=tenant_id,
                role=MembreTenant.Role.ADMINISTRATEUR,
                actif=True,
            ).exists()
            if not is_admin:
                if not _est_formateur_de_promotion(request.user, promotion.id):
                    raise serializers.ValidationError({
                        "promotion": "Vous n'êtes pas affecté à cette promotion."
                    })

        date_debut = attrs.get("date_debut", getattr(self.instance, "date_debut", None))
        date_limite = attrs.get("date_limite", getattr(self.instance, "date_limite", None))

        if date_debut and date_limite and date_limite < date_debut:
            raise serializers.ValidationError({
                "date_limite": "La date limite doit être postérieure ou égale à la date de début."
            })

        competences = attrs.get("competences")
        if competences is not None:
            formation_id = promotion.formation_id
            invalides = [
                c.id for c in competences
                if c.module.formation_id != formation_id
            ]
            if invalides:
                raise serializers.ValidationError({
                    "competences": "Toutes les compétences doivent appartenir à la formation de la promotion."
                })

        if "titre" in attrs:
            titre = attrs["titre"].strip()
            if not titre:
                raise serializers.ValidationError({"titre": "Le titre ne peut pas être vide."})
            attrs["titre"] = titre

        return attrs


# ─── RessourceBrief (déprécié — conservé pour compatibilité) ─────────────────

class RessourceBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = RessourceBrief
        fields = [
            "id",
            "brief",
            "titre",
            "url",
            "fichier",
            "date_creation",
        ]
        read_only_fields = [
            "id",
            "date_creation",
        ]

    def validate(self, attrs):
        tenant_id = self.context["tenant_id"]

        brief = attrs.get("brief", getattr(self.instance, "brief", None))

        if brief is None:
            raise serializers.ValidationError({"brief": "Le brief est obligatoire."})

        if str(brief.promotion.formation.tenant_id) != str(tenant_id):
            raise serializers.ValidationError({"brief": "Ce brief n'appartient pas à cet organisme."})

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
        request = self.context.get("request")

        brief = attrs.get("brief", getattr(self.instance, "brief", None))
        groupe = attrs.get("groupe", getattr(self.instance, "groupe", None))
        apprenant = attrs.get("apprenant", getattr(self.instance, "apprenant", None))

        if brief is None:
            raise serializers.ValidationError({"brief": "Le brief est obligatoire."})

        if str(brief.promotion.formation.tenant_id) != str(tenant_id):
            raise serializers.ValidationError({"brief": "Ce brief n'appartient pas à cet organisme."})

        # Le formateur doit être affecté à la promotion du brief
        if request and request.user and not request.user.est_admin_saas:
            is_admin = MembreTenant.objects.filter(
                utilisateur=request.user,
                tenant_id=tenant_id,
                role=MembreTenant.Role.ADMINISTRATEUR,
                actif=True,
            ).exists()
            if not is_admin:
                if not _est_formateur_de_promotion(request.user, brief.promotion_id):
                    raise serializers.ValidationError({
                        "brief": "Vous n'êtes pas affecté à la promotion de ce brief."
                    })

        # Règle : au moins une cible doit être renseignée.
        # apprenant seul, groupe seul, ou les deux sont tous acceptés.
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
            if str(groupe.promotion.formation.tenant_id) != str(tenant_id):
                raise serializers.ValidationError({
                    "groupe": "Ce groupe n'appartient pas à cet organisme."
                })

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
        if self.instance is None:
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

        # Assignation individuelle
        if assignation.apprenant_id:
            if assignation.apprenant_id != utilisateur.id:
                raise serializers.ValidationError({
                    "assignation": "Vous ne pouvez pas déposer un livrable pour cette assignation."
                })

            inscription_active = (
                assignation.brief.promotion.inscriptions.filter(
                    apprenant=utilisateur,
                    actif=True,
                ).exists()
            )

            if not inscription_active:
                raise serializers.ValidationError({
                    "assignation": "Vous n'êtes plus inscrit activement à cette promotion."
                })

        # Assignation de groupe
        elif assignation.groupe_id:
            membre_groupe = assignation.groupe.membres.filter(
                apprenant=utilisateur,
            ).exists()

            if not membre_groupe:
                raise serializers.ValidationError({
                    "assignation": "Vous n'êtes pas membre de ce groupe."
                })

            inscription_active = (
                assignation.brief.promotion.inscriptions.filter(
                    apprenant=utilisateur,
                    actif=True,
                ).exists()
            )

            if not inscription_active:
                raise serializers.ValidationError({
                    "assignation": "Vous n'êtes plus inscrit activement à cette promotion."
                })

        else:
            raise serializers.ValidationError({
                "assignation": "Cette assignation ne possède aucune cible valide."
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
