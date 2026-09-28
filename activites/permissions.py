from rest_framework.permissions import BasePermission

from accounts.models import MembreTenant

# Lecture par tout membre actif / écriture par un formateur : permissions
# communes, définies dans pedagogie.
from pedagogie.permissions import IsFormateurOrganisme, IsMembreOrganisme  # noqa: F401


def _est_membre_actif(user, tenant_id, roles):
    return MembreTenant.objects.filter(
        utilisateur=user,
        tenant_id=tenant_id,
        role__in=roles,
        actif=True,
    ).exists()


class _PermissionOrganisme(BasePermission):
    """Membre actif du tenant avec l'un des rôles donnés. Admin SaaS exclu."""

    roles = ()

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.est_admin_saas:
            return False

        tenant_id = view.kwargs.get("tenant_id")
        if not tenant_id:
            return False

        return _est_membre_actif(request.user, tenant_id, self.roles)


class CanManageRessource(_PermissionOrganisme):
    """
    Création d'une ressource : Formateur ou Admin organisme.
    Modification / suppression : limitées au créateur ou à l'admin (vue).
    """

    message = "Vous n'avez pas les droits pour gérer les ressources de cet organisme."
    roles = (MembreTenant.Role.FORMATEUR, MembreTenant.Role.ADMINISTRATEUR)


class CanCreateLivrable(_PermissionOrganisme):
    """Dépôt de livrable : Apprenant uniquement."""

    message = "Seul un apprenant autorisé peut déposer un livrable."
    roles = (MembreTenant.Role.APPRENANT,)
