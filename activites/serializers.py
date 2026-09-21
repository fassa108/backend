from django.contrib.auth import get_user_model
from rest_framework import serializers

from accounts.models import MembreTenant
from pedagogie.models import Competence, Groupe, Promotion

from .models import Assignation, Brief, RessourceBrief


User = get_user_model()


from django.contrib.auth import get_user_model
from rest_framework import serializers

from accounts.models import MembreTenant
from pedagogie.models import Competence

from .models import Assignation, Brief, RessourceBrief


User = get_user_model()


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

        promotion = attrs.get(
            "promotion",
            getattr(self.instance, "promotion", None),
        )

        if promotion is None:
            raise serializers.ValidationError({
                "promotion": "La promotion est obligatoire."
            })

        # La promotion doit appartenir au tenant de l'URL.
        if promotion.formation.tenant_id != tenant_id:
            raise serializers.ValidationError({
                "promotion": (
                    "Cette promotion n'appartient pas à cet organisme."
                )
            })

        date_debut = attrs.get(
            "date_debut",
            getattr(self.instance, "date_debut", None),
        )
        date_limite = attrs.get(
            "date_limite",
            getattr(self.instance, "date_limite", None),
        )

        if date_debut and date_limite and date_limite < date_debut:
            raise serializers.ValidationError({
                "date_limite": (
                    "La date limite doit être postérieure "
                    "ou égale à la date de début."
                )
            })

        # Les compétences doivent appartenir à la même formation
        # que la promotion du brief.
        competences = attrs.get("competences")

        if competences is not None:
            formation_id = promotion.formation_id

            invalides = [
                competence.id
                for competence in competences
                if competence.module.formation_id != formation_id
            ]

            if invalides:
                raise serializers.ValidationError({
                    "competences": (
                        "Toutes les compétences doivent appartenir "
                        "à la formation de la promotion."
                    )
                })

        if "titre" in attrs:
            titre = attrs["titre"].strip()

            if not titre:
                raise serializers.ValidationError({
                    "titre": "Le titre ne peut pas être vide."
                })

            attrs["titre"] = titre

        return attrs


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

        brief = attrs.get(
            "brief",
            getattr(self.instance, "brief", None),
        )

        if brief is None:
            raise serializers.ValidationError({
                "brief": "Le brief est obligatoire."
            })

        # Le brief doit appartenir au tenant de l'URL.
        if brief.promotion.formation.tenant_id != tenant_id:
            raise serializers.ValidationError({
                "brief": "Ce brief n'appartient pas à cet organisme."
            })

        url = attrs.get(
            "url",
            getattr(self.instance, "url", None),
        )

        fichier = attrs.get(
            "fichier",
            getattr(self.instance, "fichier", None),
        )

        # Exactement une source doit être renseignée.
        if bool(url) == bool(fichier):
            raise serializers.ValidationError({
                "url": (
                    "Une seule source doit être renseignée : "
                    "une URL ou un fichier."
                ),
                "fichier": (
                    "Une seule source doit être renseignée : "
                    "une URL ou un fichier."
                ),
            })

        if "titre" in attrs:
            titre = attrs["titre"].strip()

            if not titre:
                raise serializers.ValidationError({
                    "titre": "Le titre ne peut pas être vide."
                })

            attrs["titre"] = titre

        # Taille maximale : 5 Mo.
        if fichier and fichier.size > 5 * 1024 * 1024:
            raise serializers.ValidationError({
                "fichier": "Le fichier ne doit pas dépasser 5 Mo."
            })

        return attrs


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

        brief = attrs.get(
            "brief",
            getattr(self.instance, "brief", None),
        )

        groupe = attrs.get(
            "groupe",
            getattr(self.instance, "groupe", None),
        )

        apprenant = attrs.get(
            "apprenant",
            getattr(self.instance, "apprenant", None),
        )

        if brief is None:
            raise serializers.ValidationError({
                "brief": "Le brief est obligatoire."
            })

        # Le brief doit appartenir au tenant.
        if brief.promotion.formation.tenant_id != tenant_id:
            raise serializers.ValidationError({
                "brief": (
                    "Ce brief n'appartient pas à cet organisme."
                )
            })

        # Exactement une cible doit être renseignée.
        if bool(groupe) == bool(apprenant):
            raise serializers.ValidationError({
                "groupe": (
                    "Une assignation doit cibler soit un groupe, "
                    "soit un apprenant."
                ),
                "apprenant": (
                    "Une assignation doit cibler soit un groupe, "
                    "soit un apprenant."
                ),
            })

        promotion = brief.promotion

        if groupe:
            # Le groupe doit appartenir à la promotion du brief.
            if groupe.promotion_id != promotion.id:
                raise serializers.ValidationError({
                    "groupe": (
                        "Le groupe doit appartenir à la promotion "
                        "du brief."
                    )
                })

            # Le groupe doit également appartenir au tenant.
            if groupe.promotion.formation.tenant_id != tenant_id:
                raise serializers.ValidationError({
                    "groupe": (
                        "Ce groupe n'appartient pas à cet organisme."
                    )
                })

        if apprenant:
            # L'utilisateur doit être actif.
            if not apprenant.is_active:
                raise serializers.ValidationError({
                    "apprenant": "Cet utilisateur est inactif."
                })

            # Il doit être apprenant actif de cet organisme.
            membre = MembreTenant.objects.filter(
                utilisateur=apprenant,
                tenant_id=tenant_id,
                role=MembreTenant.Role.APPRENANT,
                actif=True,
            ).exists()

            if not membre:
                raise serializers.ValidationError({
                    "apprenant": (
                        "Cet utilisateur n'est pas un apprenant "
                        "actif de cet organisme."
                    )
                })

            # Il doit être inscrit activement à la promotion.
            inscription = promotion.inscriptions.filter(
                apprenant=apprenant,
                actif=True,
            ).exists()

            if not inscription:
                raise serializers.ValidationError({
                    "apprenant": (
                        "Cet apprenant n'est pas inscrit activement "
                        "à la promotion du brief."
                    )
                })

        return attrs

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
        brief = attrs.get(
            "brief",
            getattr(self.instance, "brief", None),
        )

        if brief is None:
            raise serializers.ValidationError({
                "brief": "Le brief est obligatoire."
            })

        url = attrs.get(
            "url",
            getattr(self.instance, "url", None),
        )
        fichier = attrs.get(
            "fichier",
            getattr(self.instance, "fichier", None),
        )

        # Une seule source doit être renseignée.
        if bool(url) == bool(fichier):
            raise serializers.ValidationError({
                "url": (
                    "Une seule source doit être renseignée : "
                    "une URL ou un fichier."
                ),
                "fichier": (
                    "Une seule source doit être renseignée : "
                    "une URL ou un fichier."
                ),
            })

        if "titre" in attrs:
            titre = attrs["titre"].strip()

            if not titre:
                raise serializers.ValidationError({
                    "titre": "Le titre ne peut pas être vide."
                })

            attrs["titre"] = titre

        # Limite de 5 Mo.
        if fichier and fichier.size > 5 * 1024 * 1024:
            raise serializers.ValidationError({
                "fichier": "Le fichier ne doit pas dépasser 5 Mo."
            })

        return attrs


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

        brief = attrs.get(
            "brief",
            getattr(self.instance, "brief", None),
        )
        groupe = attrs.get(
            "groupe",
            getattr(self.instance, "groupe", None),
        )
        apprenant = attrs.get(
            "apprenant",
            getattr(self.instance, "apprenant", None),
        )

        if brief is None:
            raise serializers.ValidationError({
                "brief": "Le brief est obligatoire."
            })

        # Le brief doit appartenir au tenant.
        if brief.promotion.formation.tenant_id != tenant_id:
            raise serializers.ValidationError({
                "brief": (
                    "Ce brief n'appartient pas à cet organisme."
                )
            })

        # Exactement une cible.
        if bool(groupe) == bool(apprenant):
            raise serializers.ValidationError({
                "groupe": (
                    "Une assignation doit cibler soit un groupe, "
                    "soit un apprenant."
                ),
                "apprenant": (
                    "Une assignation doit cibler soit un groupe, "
                    "soit un apprenant."
                ),
            })

        promotion = brief.promotion

        if groupe:
            if groupe.promotion_id != promotion.id:
                raise serializers.ValidationError({
                    "groupe": (
                        "Le groupe doit appartenir à la promotion "
                        "du brief."
                    )
                })

            if groupe.promotion.formation.tenant_id != tenant_id:
                raise serializers.ValidationError({
                    "groupe": (
                        "Ce groupe n'appartient pas à cet organisme."
                    )
                })

        if apprenant:
            if not apprenant.is_active:
                raise serializers.ValidationError({
                    "apprenant": "Cet utilisateur est inactif."
                })

            membre = MembreTenant.objects.filter(
                utilisateur=apprenant,
                tenant_id=tenant_id,
                role=MembreTenant.Role.APPRENANT,
                actif=True,
            ).exists()

            if not membre:
                raise serializers.ValidationError({
                    "apprenant": (
                        "Cet utilisateur n'est pas un apprenant "
                        "actif de cet organisme."
                    )
                })

            inscription = promotion.inscriptions.filter(
                apprenant=apprenant,
                actif=True,
            ).exists()

            if not inscription:
                raise serializers.ValidationError({
                    "apprenant": (
                        "Cet apprenant n'est pas inscrit activement "
                        "à la promotion du brief."
                    )
                })
        if self.instance is None:
            if groupe and Assignation.objects.filter(
                brief=brief,
                groupe=groupe,
            ).exists():
                raise serializers.ValidationError({
                    "groupe": "Ce groupe est déjà assigné à ce brief."
                })

            if apprenant and Assignation.objects.filter(
                brief=brief,
                apprenant=apprenant,
            ).exists():
                raise serializers.ValidationError({
                    "apprenant": "Cet apprenant est déjà assigné à ce brief."
                })
        return attrs