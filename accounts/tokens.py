from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError

from .models import AccountActivationToken


class ActivationTokenService:

    @staticmethod
    def get_valid_token(token):
        """
        Recherche un token d'activation et vérifie
        qu'il peut encore être utilisé.
        """

        try:
            activation_token = AccountActivationToken.objects.select_related(
                "utilisateur"
            ).get(token=token)
        # Jeton mal formé (lien tronqué) : même réponse qu'un jeton inconnu
        except (AccountActivationToken.DoesNotExist, DjangoValidationError):
            raise ValidationError(
                "Le lien d'activation est invalide."
            )

        if not activation_token.est_valide():
            raise ValidationError(
                "Le lien d'activation est expiré ou a déjà été utilisé."
            )

        return activation_token