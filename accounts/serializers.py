from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from pedagogie.models import FormateurPromotion, InscriptionPromotion

from .models import Utilisateur, MembreTenant

class UtilisateurSerializer(serializers.ModelSerializer):
    """
    Serializer utilisé pour retourner les informations publiques
    d'un utilisateur.

    Le mot de passe n'est volontairement jamais exposé.
    """

    class Meta:
        model = Utilisateur
        fields = (
            "id",
            "nom",
            "prenom",
            "email",
            "actif",
            "est_admin_saas",
            "date_creation",
        )
        read_only_fields = (
            "id",
            "actif",
            "est_admin_saas",
            "date_creation",
        )


class ActivationSerializer(serializers.Serializer):
    """
    Données envoyées lorsqu'un utilisateur active son compte.
    """

    token = serializers.CharField()

    # Les règles de sécurité (AUTH_PASSWORD_VALIDATORS) sont
    # appliquées dans AccountService, une fois l'utilisateur connu.
    password = serializers.CharField(
        write_only=True
    )

    password_confirm = serializers.CharField(
        write_only=True
    )

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({
                "password_confirm": "Les mots de passe ne correspondent pas."
            })

        return attrs


class ConnexionJWTSerializer(TokenObtainPairSerializer):
    """
    Serializer personnalisé pour la connexion JWT.

    L'utilisateur se connecte avec son email et son mot de passe.
    """

    username_field = "email"

    # Même message que le compte soit inexistant, inactif
    # ou le mot de passe faux : on ne révèle pas quels comptes existent.
    default_error_messages = {
        "no_active_account": "Email ou mot de passe incorrect."
    }

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # Informations présentes dans le token JWT.
        token["user_id"] = user.id
        token["email"] = user.email

        return token

    def validate(self, attrs):
        # Les comptes inactifs sont refusés par super().validate()
        # avec le message « no_active_account ».
        data = super().validate(attrs)

        # Organismes de l'utilisateur, y compris ceux où son accès
        # est suspendu (« actif » = False) : le frontend affiche alors
        # la page « accès suspendu » au lieu de « aucun organisme ».
        membres_tenants = (
            MembreTenant.objects
            .filter(utilisateur=self.user)
            .select_related("tenant")
        )

        # Informations de l'utilisateur connecté.
        data["utilisateur"] = {
            "id": self.user.id,
            "nom": self.user.nom,
            "prenom": self.user.prenom,
            "email": self.user.email,
            "est_admin_saas": self.user.est_admin_saas,
        }

        # Organismes + rôle de l'utilisateur dans chacun.
        # « statut » permet au frontend d'afficher la page
        # « organisme suspendu ».
        data["tenants"] = [
            {
                "id": membre.tenant.id,
                "nom": membre.tenant.nom,
                "code": membre.tenant.code,
                "role": membre.role,
                "statut": membre.tenant.statut,
                "actif": membre.actif,
            }
            for membre in membres_tenants
        ]

        return data
    
class TenantConnexionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    nom = serializers.CharField()
    code = serializers.CharField()
    role = serializers.CharField()
    statut = serializers.BooleanField()
    actif = serializers.BooleanField()


class UtilisateurConnexionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    nom = serializers.CharField()
    prenom = serializers.CharField()
    email = serializers.EmailField()
    est_admin_saas = serializers.BooleanField()


class ConnexionResponseSerializer(serializers.Serializer):
    refresh = serializers.CharField()
    access = serializers.CharField()
    utilisateur = UtilisateurConnexionSerializer()
    tenants = TenantConnexionSerializer(many=True)

class DemandeResetPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()



class ResetPasswordSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    # Les règles de sécurité (AUTH_PASSWORD_VALIDATORS) sont
    # appliquées dans AccountService, une fois l'utilisateur connu.
    password = serializers.CharField(
        write_only=True
    )
    password_confirm = serializers.CharField(
        write_only=True
    )

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({
                "password_confirm": "Les mots de passe ne correspondent pas."
            })

        return attrs





class MembreTenantSerializer(serializers.ModelSerializer):
    utilisateur_nom = serializers.CharField(source="utilisateur.nom", read_only=True)
    utilisateur_prenom = serializers.CharField(source="utilisateur.prenom", read_only=True)
    utilisateur_email = serializers.EmailField(source="utilisateur.email", read_only=True)
    utilisateur_actif = serializers.BooleanField(source="utilisateur.actif", read_only=True)
    promotions_en_cours = serializers.SerializerMethodField()

    class Meta:
        model = MembreTenant
        fields = [
            "id",
            "utilisateur",
            "utilisateur_nom",
            "utilisateur_prenom",
            "utilisateur_email",
            "utilisateur_actif",
            "tenant",
            "role",
            "actif",
            "date_ajout",
            "promotions_en_cours",
        ]
        read_only_fields = [
            "id",
            "utilisateur",
            "utilisateur_nom",
            "utilisateur_prenom",
            "utilisateur_email",
            "utilisateur_actif",
            "tenant",
            "date_ajout",
            "promotions_en_cours",
        ]

    def get_promotions_en_cours(self, membre):
        """
        Promotions dans lesquelles le membre intervient dans cet organisme :
        inscriptions actives (apprenant) ou affectations (formateur).
        Sert à avertir l'admin avant une suspension.

        Pour une liste, la vue fournit ces données déjà calculées dans
        le contexte (« promotions_par_membre ») : une requête par liste
        au lieu d'une requête par membre.
        """
        precalcul = self.context.get("promotions_par_membre")
        if precalcul is not None:
            return precalcul.get((membre.utilisateur_id, membre.role), [])

        if membre.role == MembreTenant.Role.APPRENANT:
            liens = InscriptionPromotion.objects.filter(
                apprenant_id=membre.utilisateur_id,
                promotion__formation__tenant_id=membre.tenant_id,
                actif=True,
            )
        elif membre.role == MembreTenant.Role.FORMATEUR:
            liens = FormateurPromotion.objects.filter(
                formateur_id=membre.utilisateur_id,
                promotion__formation__tenant_id=membre.tenant_id,
            )
        else:
            return []

        return [
            {"id": promotion_id, "nom": nom}
            for promotion_id, nom in liens.values_list("promotion_id", "promotion__nom")
        ]

    def validate(self, attrs):
        membre = self.instance
        if membre is None:
            return attrs

        nouveau_role = attrs.get("role", membre.role)
        nouvel_actif = attrs.get("actif", membre.actif)

        # L'organisme doit toujours garder au moins un admin actif.
        perd_admin = (
            membre.role == MembreTenant.Role.ADMINISTRATEUR
            and membre.actif
            and (
                nouveau_role != MembreTenant.Role.ADMINISTRATEUR
                or not nouvel_actif
            )
        )
        if perd_admin and not MembreTenant.objects.filter(
            tenant_id=membre.tenant_id,
            role=MembreTenant.Role.ADMINISTRATEUR,
            actif=True,
        ).exclude(pk=membre.pk).exists():
            raise serializers.ValidationError(
                "L'organisme doit garder au moins un administrateur actif."
            )

        if nouveau_role != membre.role:
            if (
                membre.role == MembreTenant.Role.APPRENANT
                and InscriptionPromotion.objects.filter(
                    apprenant_id=membre.utilisateur_id,
                    promotion__formation__tenant_id=membre.tenant_id,
                    actif=True,
                ).exists()
            ):
                raise serializers.ValidationError({
                    "role": (
                        "Cet apprenant a une inscription active : "
                        "désinscrivez-le avant de changer son rôle."
                    )
                })

            if (
                membre.role == MembreTenant.Role.FORMATEUR
                and FormateurPromotion.objects.filter(
                    formateur_id=membre.utilisateur_id,
                    promotion__formation__tenant_id=membre.tenant_id,
                ).exists()
            ):
                raise serializers.ValidationError({
                    "role": (
                        "Ce formateur est affecté à des promotions : "
                        "retirez ses affectations avant de changer son rôle."
                    )
                })

        return attrs



class AjouterMembreSerializer(serializers.Serializer):
    nom = serializers.CharField(
        max_length=100,
        required=False
    )
    prenom = serializers.CharField(
        max_length=100,
        required=False
    )
    email = serializers.EmailField(
        required=True
    )
    role = serializers.ChoiceField(
        choices=MembreTenant.Role.choices
    )

    def validate_email(self, value):
        value = value.strip().lower()
        if Utilisateur.objects.filter(email__iexact=value, est_admin_saas=True).exists():
            raise serializers.ValidationError(
                "Cette adresse ne peut pas être membre d'un organisme."
            )
        return value

    def validate(self, attrs):
        email = attrs["email"]

        utilisateur_existant = Utilisateur.objects.filter(
            email__iexact=email
        ).first()

        if utilisateur_existant and MembreTenant.objects.filter(
            utilisateur=utilisateur_existant,
            tenant_id=self.context["tenant_id"],
        ).exists():
            raise serializers.ValidationError({
                "email": "Cet utilisateur appartient déjà à cet organisme."
            })

        # Si le compte n'existe pas, nom et prénom sont obligatoires
        # (les deux erreurs sont renvoyées ensemble).
        if not utilisateur_existant:
            erreurs = {}
            if not attrs.get("prenom", "").strip():
                erreurs["prenom"] = "Le prénom est obligatoire pour un nouvel utilisateur."
            if not attrs.get("nom", "").strip():
                erreurs["nom"] = "Le nom est obligatoire pour un nouvel utilisateur."
            if erreurs:
                raise serializers.ValidationError(erreurs)

        return attrs