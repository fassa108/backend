from django.contrib.auth import authenticate
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

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


class InvitationSerializer(serializers.Serializer):
    """
    Données nécessaires pour inviter un utilisateur.

    Le mot de passe n'est pas demandé ici :
    l'utilisateur le définira lors de l'activation de son compte.
    """

    nom = serializers.CharField(max_length=100)
    prenom = serializers.CharField(max_length=100)
    email = serializers.EmailField()
    tenant_id = serializers.IntegerField()
    role = serializers.ChoiceField(
        choices=(
            ("ADMINISTRATEUR", "Administrateur"),
            ("FORMATEUR", "Formateur"),
            ("APPRENANT", "Apprenant"),
        )
    )


class ActivationSerializer(serializers.Serializer):
    """
    Données envoyées lorsqu'un utilisateur active son compte.
    """

    token = serializers.CharField()

    password = serializers.CharField(
        write_only=True,
        min_length=8
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


class ConnexionSerializer(serializers.Serializer):
    """
    Authentification d'un utilisateur avec son email et son mot de passe.
    """

    email = serializers.EmailField()
    password = serializers.CharField(
        write_only=True
    )

    def validate(self, attrs):
        email = attrs["email"]
        password = attrs["password"]

        utilisateur = authenticate(
            email=email,
            password=password
        )

        if utilisateur is None:
            raise serializers.ValidationError(
                "Email ou mot de passe incorrect."
            )

        if not utilisateur.actif:
            raise serializers.ValidationError(
                "Ce compte n'est pas encore activé."
            )

        attrs["utilisateur"] = utilisateur

        return attrs


class ConnexionJWTSerializer(TokenObtainPairSerializer):
    """
    Serializer personnalisé pour la connexion JWT.

    L'utilisateur se connecte avec son email et son mot de passe.
    """

    username_field = "email"

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # Informations présentes dans le token JWT.
        token["user_id"] = user.id
        token["email"] = user.email

        return token

    def validate(self, attrs):
        data = super().validate(attrs)

        # Vérification du compte utilisateur.
        if not self.user.actif:
            raise serializers.ValidationError(
                "Ce compte n'est pas actif."
            )

        # Récupération des organismes auxquels
        # l'utilisateur a actuellement accès.
        membres_tenants = (
            MembreTenant.objects
            .filter(
                utilisateur=self.user,
                actif=True
            )
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
        data["tenants"] = [
            {
                "id": membre.tenant.id,
                "nom": membre.tenant.nom,
                "code": membre.tenant.code,
                "role": membre.role,
            }
            for membre in membres_tenants
        ]

        return data
    
class TenantConnexionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    nom = serializers.CharField()
    code = serializers.CharField()
    role = serializers.CharField()


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
    password = serializers.CharField(
        write_only=True,
        min_length=8
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
    class Meta:
        model = MembreTenant
        fields = [
            "id",
            "utilisateur",
            "tenant",
            "role",
            "actif",
            "date_ajout",
        ]
        read_only_fields = [
            "id",
            "utilisateur",
            "tenant",
            "date_ajout",
        ]



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
        return value.strip().lower()

    def validate(self, attrs):
        email = attrs["email"]

        utilisateur_existant = Utilisateur.objects.filter(
            email=email
        ).first()

        # Si le compte n'existe pas, nom et prénom sont obligatoires
        if not utilisateur_existant:
            if not attrs.get("nom"):
                raise serializers.ValidationError({
                    "nom": "Le nom est obligatoire pour un nouvel utilisateur."
                })

            if not attrs.get("prenom"):
                raise serializers.ValidationError({
                    "prenom": "Le prénom est obligatoire pour un nouvel utilisateur."
                })

        return attrs