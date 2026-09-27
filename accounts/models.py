from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower

import uuid
from django.utils import timezone
from datetime import timedelta

class UtilisateurManager(BaseUserManager):
    """
    Manager personnalisé pour le modèle Utilisateur.

    Django utilise ce manager pour créer les utilisateurs et les
    superutilisateurs, notamment via la commande createsuperuser.
    """

    def create_user(self, email, password=None, **extra_fields):
        """
        Crée un utilisateur classique.

        L'email est utilisé comme identifiant principal de connexion.
        """

        # Un utilisateur doit obligatoirement avoir une adresse email.
        if not email:
            raise ValueError("L'adresse email est obligatoire.")

        # L'email est stocké entièrement en minuscules :
        # « Awa@x.com » et « awa@x.com » désignent le même compte.
        email = self.normalize_email(email).strip().lower()

        # Création de l'instance utilisateur.
        user = self.model(
            email=email,
            **extra_fields
        )

        # Le mot de passe ne doit jamais être enregistré en clair.
        # set_password() le transforme en hash sécurisé.
        if password:
            user.set_password(password)

        # Enregistre l'utilisateur dans la base de données.
        user.save(using=self._db)

        return user

    def get_by_natural_key(self, email):
        """
        Recherche insensible à la casse, utilisée à la connexion.
        """
        return self.get(email__iexact=email)

    def create_superuser(self, email, password=None, **extra_fields):
        """
        Crée un superutilisateur Django.

        Le superutilisateur est principalement utilisé pour
        l'administration technique de l'application.
        """

        # Ces valeurs permettent à Django de reconnaître
        # l'utilisateur comme administrateur système.
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        # Contrairement à un utilisateur invité, le superutilisateur
        # doit être actif immédiatement.
        extra_fields.setdefault("actif", True)

        return self.create_user(
            email=email,
            password=password,
            **extra_fields
        )


class Utilisateur(AbstractBaseUser):
    """
    Modèle utilisateur principal de Synapse.

    L'email est utilisé comme identifiant de connexion.
    Le rôle n'est volontairement pas stocké ici :
    il sera porté par MembreTenant, car un utilisateur peut
    appartenir à plusieurs organismes avec des rôles différents.
    """

    # Informations personnelles de l'utilisateur.
    nom = models.CharField(max_length=100)
    prenom = models.CharField(max_length=100)

    # Identifiant de connexion.
    # Chaque adresse email doit être unique.
    email = models.EmailField(unique=True)

    # Indique si le compte a été activé.
    #
    # Lorsqu'un utilisateur est invité, il est créé avec
    # actif=False et ne peut pas encore se connecter.
    actif = models.BooleanField(default=False)

    # Permet d'identifier un administrateur global de la plateforme.
    # Il n'est pas rattaché à un tenant pour exercer ses droits.
    est_admin_saas = models.BooleanField(default=False)

    # Date de création du compte.
    date_creation = models.DateTimeField(auto_now_add=True)

    # Indique si l'utilisateur peut accéder à l'administration
    # Django (ce n'est pas le rôle métier de Synapse).
    is_staff = models.BooleanField(default=False)

    # Indique si l'utilisateur possède les privilèges
    # de superutilisateur Django.
    is_superuser = models.BooleanField(default=False)

    # Utilisation de notre manager personnalisé.
    objects = UtilisateurManager()

    # Django utilisera l'email au lieu d'un username
    # pour authentifier l'utilisateur.
    USERNAME_FIELD = "email"

    # Champs obligatoires lors de la création d'un
    # superutilisateur avec createsuperuser.
    REQUIRED_FIELDS = ["nom", "prenom"]

    class Meta:
        constraints = [
            # Unicité de l'email indépendamment de la casse.
            models.UniqueConstraint(
                Lower("email"),
                name="unique_email_insensible_casse",
            ),
        ]

    def __str__(self):
        """
        Représentation lisible de l'utilisateur.
        """
        return f"{self.prenom} - {self.nom}"

    @property
    def is_active(self):
        """
        Indique à Django si le compte est actif.

        On utilise notre champ 'actif' comme source de vérité.
        """
        return self.actif



class MembreTenant(models.Model):
    """
    Représente l'appartenance d'un utilisateur à un organisme.

    Le rôle est défini ici et non dans Utilisateur, car un même
    utilisateur peut appartenir à plusieurs organismes avec
    des rôles différents.
    """

    class Role(models.TextChoices):
        ADMINISTRATEUR = "ADMINISTRATEUR", "Administrateur"
        FORMATEUR = "FORMATEUR", "Formateur"
        APPRENANT = "APPRENANT", "Apprenant"

    # Utilisateur concerné par cette appartenance.
    utilisateur = models.ForeignKey(
        "Utilisateur",
        on_delete=models.CASCADE,
        related_name="membres_tenants"
    )

    # Organisme auquel l'utilisateur appartient.
    #
    # On utilise une référence vers Tenant sans importer
    # directement le modèle afin d'éviter les imports circulaires.
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="membres"
    )

    # Rôle de l'utilisateur dans cet organisme.
    role = models.CharField(
        max_length=20,
        choices=Role.choices
    )

    # Permet de désactiver l'accès de l'utilisateur à cet
    # organisme sans supprimer son compte.
    actif = models.BooleanField(default=True)

    # Date à laquelle l'utilisateur a rejoint l'organisme.
    date_ajout = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Un utilisateur ne peut appartenir qu'une seule fois
        # au même organisme.
        constraints = [
            models.UniqueConstraint(
                fields=["utilisateur", "tenant"],
                name="unique_utilisateur_tenant"
            )
        ]

    def __str__(self):
        return f"{self.utilisateur} - {self.tenant} - {self.role}"



class AccountActivationToken(models.Model):
    """
    Token temporaire permettant à un utilisateur invité
    d'activer son compte.
    """

    utilisateur = models.ForeignKey(
        "Utilisateur",
        on_delete=models.CASCADE,
        related_name="tokens_activation"
    )

    token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False
    )

    date_expiration = models.DateTimeField()

    utilise = models.BooleanField(default=False)

    date_creation = models.DateTimeField(auto_now_add=True)

    def est_valide(self):
        """
        Un token est utilisable uniquement s'il n'a pas été utilisé
        et si sa date d'expiration n'est pas dépassée.
        """
        return (
            not self.utilise
            and timezone.now() < self.date_expiration
        )

    def __str__(self):
        return f"Token activation - {self.utilisateur.email}"