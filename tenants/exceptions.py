from rest_framework import status
from rest_framework.exceptions import APIException


ORGANISME_SUSPENDU_CODE = "organisme_suspendu"
ORGANISME_SUSPENDU_MESSAGE = "Cet organisme est suspendu."


class OrganismeSuspendu(APIException):
    """
    Levée lorsqu'un membre tente d'agir dans un organisme suspendu.

    Le frontend s'appuie sur le code « organisme_suspendu »
    pour afficher la page dédiée.
    """

    status_code = status.HTTP_403_FORBIDDEN
    default_detail = ORGANISME_SUSPENDU_MESSAGE
    default_code = ORGANISME_SUSPENDU_CODE

    def __init__(self):
        # Même format que la réponse de OrganismeSuspenduMiddleware.
        super().__init__({
            "detail": ORGANISME_SUSPENDU_MESSAGE,
            "code": ORGANISME_SUSPENDU_CODE,
        })
