from rest_framework.permissions import BasePermission

from accounts.models import MembreTenant


class IsTenantAdminOrSaaSAdmin(BasePermission):
    """
    Règle d'accès stricte pour le socle pédagogie :
    - Administrateur SaaS : CRUD complet.
    - Administrateur organisme actif : CRUD complet sur son organisme.
    - Formateur : aucun accès pour cette étape.
    - Apprenant : aucun accès pour cette étape.
    - Utilisateurs non-membres / inactifs : aucun accès.
    """

    message = "Accès réservé aux administrateurs de cet organisme ou aux administrateurs SaaS."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        # 1. Administrateur SaaS : accès complet
        if request.user.est_admin_saas:
            return True

        # 2. Administrateur organisme actif
        tenant_id = view.kwargs.get("tenant_id")
        if not tenant_id:
            return False

        return MembreTenant.objects.filter(
            utilisateur=request.user,
            tenant_id=tenant_id,
            role=MembreTenant.Role.ADMINISTRATEUR,
            actif=True,
        ).exists()
