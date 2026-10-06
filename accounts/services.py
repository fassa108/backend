from datetime import timedelta

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework.exceptions import ValidationError
from django.utils import timezone


from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes
from django.utils.encoding import force_str
from django.conf import settings

from .models import AccountActivationToken, MembreTenant, Utilisateur
from .tasks import (
    envoyer_email_reset_password,
    envoyer_email_activation,
)


def _valider_mot_de_passe(password, utilisateur):
    """
    Applique les règles de AUTH_PASSWORD_VALIDATORS
    (longueur, mots de passe courants, similarité avec l'utilisateur…).
    """
    try:
        validate_password(password, user=utilisateur)
    except DjangoValidationError as error:
        raise ValidationError({"password": error.messages})


class AccountService:

    @staticmethod
    @transaction.atomic
    def inviter_utilisateur(
        *,
        nom,
        prenom,
        email,
        tenant,
        role
    ):
        """
        Crée un compte inactif, l'associe à un organisme
        et génère son token d'activation.

        L'opération est atomique :
        si une étape échoue, les modifications précédentes
        sont annulées.
        """

        # Le compte est créé sans mot de passe utilisable.
        # L'utilisateur le définira lors de l'activation.
        utilisateur = Utilisateur.objects.create_user(
            email=email,
            nom=nom,
            prenom=prenom,
        )

        # Le compte reste désactivé jusqu'à son activation.
        utilisateur.actif = False
        utilisateur.save(update_fields=["actif"])

        # On associe l'utilisateur à l'organisme
        # avec son rôle métier.
        MembreTenant.objects.create(
            utilisateur=utilisateur,
            tenant=tenant,
            role=role,
        )

        token = AccountService.envoyer_invitation(utilisateur)

        return utilisateur, token


    @staticmethod
    @transaction.atomic
    def envoyer_invitation(utilisateur):
        """
        Génère un nouveau lien d'activation et l'envoie par email.
        Sert aussi à relancer un compte jamais activé.
        """

        # Le token précédent ne doit plus pouvoir être utilisé
        # lorsqu'une nouvelle invitation est générée.
        AccountActivationToken.objects.filter(
            utilisateur=utilisateur,
            utilise=False,
        ).update(utilise=True)

        token = AccountActivationToken.objects.create(
            utilisateur=utilisateur,
            date_expiration=timezone.now() + timedelta(hours=settings.DUREE_LIEN_ACTIVATION_HEURES),
        )

        activation_url = (
            f"{settings.FRONTEND_URL}/activate-account/"
            f"{utilisateur.id}/{token.token}"
        )

        transaction.on_commit(
            lambda: envoyer_email_activation.delay(
                utilisateur.email,
                activation_url,
            )
        )

        return token


    @staticmethod
    @transaction.atomic
    def activer_compte(
        *,
        activation_token,
        password
    ):
        """
        Active un compte après validation du token
        et définit son mot de passe.
        """

        utilisateur = activation_token.utilisateur

        _valider_mot_de_passe(password, utilisateur)

        # Le mot de passe est stocké sous forme de hash,
        # jamais en clair dans la base de données.
        utilisateur.set_password(password)

        # Le compte devient maintenant utilisable.
        utilisateur.actif = True

        utilisateur.save(
            update_fields=[
                "password",
                "actif",
            ]
        )

        # Le token devient inutilisable immédiatement
        # après une activation réussie.
        activation_token.utilise = True

        activation_token.save(
            update_fields=["utilise"]
        )

        return utilisateur

    
    @staticmethod
    def demander_reset_password(*, email):
        try:
            utilisateur = Utilisateur.objects.get(email__iexact=email)
        except Utilisateur.DoesNotExist:
            return

        uid = urlsafe_base64_encode(force_bytes(utilisateur.pk))
        token = default_token_generator.make_token(utilisateur)

        reset_url = (
            f"{settings.FRONTEND_URL}/reset-password/"
            f"{uid}/{token}"
        )

        envoyer_email_reset_password.delay(
            utilisateur.email,
            reset_url,
        )


    @staticmethod
    def reset_password(*, uid, token, password):
        try:
            user_id = force_str(urlsafe_base64_decode(uid))
            utilisateur = Utilisateur.objects.get(pk=user_id)
        except (TypeError, ValueError, OverflowError, Utilisateur.DoesNotExist):
            raise ValueError("Lien de réinitialisation invalide.")

        if not default_token_generator.check_token(utilisateur, token):
            raise ValueError("Lien de réinitialisation invalide ou expiré.")

        _valider_mot_de_passe(password, utilisateur)

        utilisateur.set_password(password)
        utilisateur.save(update_fields=["password"])

        return utilisateur


    @staticmethod
    @transaction.atomic
    def ajouter_membre_existant(
        *,
        utilisateur,
        tenant,
        role
    ):
        membre_existant = MembreTenant.objects.filter(
            utilisateur=utilisateur,
            tenant=tenant
        ).first()

        if membre_existant:
            raise ValueError(
                "Cet utilisateur appartient déjà à cet organisme."
            )

        return MembreTenant.objects.create(
            utilisateur=utilisateur,
            tenant=tenant,
            role=role
        )