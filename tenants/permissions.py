from rest_framework.permissions import BasePermission

from accounts.models import MembreTenant

from .exceptions import OrganismeSuspendu
from .models import Tenant


class IsAdminSaaS(BasePermission):
    message = "Seul un administrateur SaaS peut effectuer cette action."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.est_admin_saas
        )


class IsAdminOrganisme(BasePermission):
    message = "Vous devez être administrateur de cet organisme."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        tenant_id = view.kwargs.get("pk")

        est_admin = MembreTenant.objects.filter(
            utilisateur=request.user,
            tenant_id=tenant_id,
            role=MembreTenant.Role.ADMINISTRATEUR,
            actif=True,
        ).exists()

        if not est_admin:
            return False

        # Un organisme suspendu n'est plus gérable par son admin.
        if Tenant.objects.filter(pk=tenant_id, statut=False).exists():
            raise OrganismeSuspendu()

        return True
