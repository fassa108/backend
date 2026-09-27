from rest_framework.permissions import BasePermission
from .models import MembreTenant


class CanManageMembers(BasePermission):
    message = (
        "Vous n'avez pas les droits pour gérer les membres "
        "de cet organisme."
    )

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        # Seul l'administrateur de l'organisme gère les membres.
        # L'admin SaaS crée l'organisme avec son premier administrateur,
        # qui prend ensuite le relais.
        tenant_id = view.kwargs.get("tenant_id")

        return MembreTenant.objects.filter(
            utilisateur=request.user,
            tenant_id=tenant_id,
            role=MembreTenant.Role.ADMINISTRATEUR,
            actif=True,
        ).exists()