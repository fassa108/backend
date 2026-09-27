from django.http import JsonResponse
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, AuthenticationFailed

from accounts.models import MembreTenant

from .exceptions import (
    MEMBRE_SUSPENDU_CODE,
    MEMBRE_SUSPENDU_MESSAGE,
    ORGANISME_SUSPENDU_CODE,
    ORGANISME_SUSPENDU_MESSAGE,
)
from .models import Tenant


def _refus(message, code):
    return JsonResponse({"detail": message, "code": code}, status=403)


def _utilisateur_jwt(request):
    """
    Utilisateur authentifié par le JWT de la requête, ou None.

    Un token absent ou invalide est ignoré ici : DRF renverra le 401.
    """
    try:
        resultat = JWTAuthentication().authenticate(request)
    except (InvalidToken, AuthenticationFailed):
        return None
    return resultat[0] if resultat else None


class OrganismeSuspenduMiddleware:
    """
    Bloque toutes les routes métier d'un organisme suspendu,
    et celles d'un membre dont l'accès à l'organisme est suspendu.

    Toutes les routes rattachées à un organisme portent le paramètre
    d'URL « tenant_id » (pédagogie, activités, membres). La requête est
    refusée avant d'atteindre la vue, avec un code que le frontend utilise
    pour afficher la page dédiée :
    - « organisme_suspendu » : Tenant.statut = False ;
    - « membre_suspendu » : MembreTenant.actif = False pour cet utilisateur.

    La fiche de l'organisme (/api/tenants/<pk>/) n'utilise pas
    « tenant_id » : elle reste accessible à l'admin SaaS, et le cas
    de l'admin d'organisme est géré par sa permission.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        tenant_id = view_kwargs.get("tenant_id")

        if tenant_id is None:
            return None

        if Tenant.objects.filter(pk=tenant_id, statut=False).exists():
            return _refus(ORGANISME_SUSPENDU_MESSAGE, ORGANISME_SUSPENDU_CODE)

        utilisateur = _utilisateur_jwt(request)
        if utilisateur and MembreTenant.objects.filter(
            utilisateur=utilisateur,
            tenant_id=tenant_id,
            actif=False,
        ).exists():
            return _refus(MEMBRE_SUSPENDU_MESSAGE, MEMBRE_SUSPENDU_CODE)

        return None
