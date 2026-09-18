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
        except AccountActivationToken.DoesNotExist:
            raise ValidationError(
                "Le lien d'activation est invalide."
            )

        if not activation_token.est_valide():
            raise ValidationError(
                "Le lien d'activation est expiré ou a déjà été utilisé."
            )

        return activation_token