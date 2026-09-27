from django.http import JsonResponse

from .exceptions import ORGANISME_SUSPENDU_CODE, ORGANISME_SUSPENDU_MESSAGE
from .models import Tenant


class OrganismeSuspenduMiddleware:
    """
    Bloque toutes les routes métier d'un organisme suspendu.

    Toutes les routes rattachées à un organisme portent le paramètre
    d'URL « tenant_id » (pédagogie, activités, membres). Si cet organisme
    est suspendu, la requête est refusée avant d'atteindre la vue.

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
            return JsonResponse(
                {
                    "detail": ORGANISME_SUSPENDU_MESSAGE,
                    "code": ORGANISME_SUSPENDU_CODE,
                },
                status=403,
            )

        return None
