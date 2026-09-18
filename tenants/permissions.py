from rest_framework.permissions import BasePermission

from accounts.models import MembreTenant


class IsAdminSaaS(BasePermission):
    message = "Seul un administrateur SaaS peut effectuer cette action."

    def has_permission(self, request, view):
        return (
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

        return MembreTenant.objects.filter(
            utilisateur=request.user,
            tenant_id=tenant_id,
            role=MembreTenant.Role.ADMINISTRATEUR,
            actif=True,
        ).exists()