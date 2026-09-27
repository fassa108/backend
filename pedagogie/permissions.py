from rest_framework.permissions import BasePermission

from accounts.models import MembreTenant


def _est_admin_organisme(user, tenant_id):
    """Vérifie que l'utilisateur est Admin organisme actif du tenant."""
    return MembreTenant.objects.filter(
        utilisateur=user,
        tenant_id=tenant_id,
        role=MembreTenant.Role.ADMINISTRATEUR,
        actif=True,
    ).exists()


def _est_formateur_actif(user, tenant_id):
    """Vérifie que l'utilisateur est Formateur actif du tenant."""
    return MembreTenant.objects.filter(
        utilisateur=user,
        tenant_id=tenant_id,
        role=MembreTenant.Role.FORMATEUR,
        actif=True,
    ).exists()


def _est_formateur_de_promotion(user, promotion_id):
    """Vérifie que le formateur est affecté à la promotion donnée."""
    from .models import FormateurPromotion
    return FormateurPromotion.objects.filter(
        formateur=user,
        promotion_id=promotion_id,
    ).exists()


class IsAdminOrganisme(BasePermission):
    """
    Admin organisme uniquement.
    L'Admin SaaS et le Formateur sont exclus.
    """

    message = "Accès réservé aux administrateurs de cet organisme."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        # Admin SaaS explicitement exclu des opérations métier organisme
        if request.user.est_admin_saas:
            return False

        tenant_id = view.kwargs.get("tenant_id")
        if not tenant_id:
            return False

        return _est_admin_organisme(request.user, tenant_id)


class IsAdminOrganismeOrFormateur(BasePermission):
    """
    Admin organisme OU Formateur actif du tenant.
    Utilisé pour les opérations où les deux rôles ont un accès (ex : lecture).
    L'Admin SaaS est exclu.
    """

    message = "Accès réservé aux administrateurs et formateurs de cet organisme."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.est_admin_saas:
            return False

        tenant_id = view.kwargs.get("tenant_id")
        if not tenant_id:
            return False

        return (
            _est_admin_organisme(request.user, tenant_id)
            or _est_formateur_actif(request.user, tenant_id)
        )


class IsFormateurDePromotion(BasePermission):
    """
    Formateur affecté à la promotion spécifique concernée par la requête.
    Utilisé pour les opérations sur groupes, briefs, assignations d'une promotion.
    L'Admin SaaS est exclu.
    """

    message = "Vous n'êtes pas affecté à cette promotion."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.est_admin_saas:
            return False

        tenant_id = view.kwargs.get("tenant_id")
        if not tenant_id:
            return False

        if not _est_formateur_actif(request.user, tenant_id):
            return False

        # La vérification promotion_id se fait dans le queryset et la validation.
        # Cette permission valide le rôle au niveau du tenant.
        return True


class IsTenantAdminOrSaaSAdminOrAssignedLearner(BasePermission):
    """
    Admin organisme OU Admin SaaS OU Apprenant actif du tenant.
    Conservé pour compatibilité avec l'existant.
    """

    message = (
        "Accès réservé aux administrateurs ou à l'apprenant "
        "concerné par cette assignation."
    )

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.est_admin_saas:
            return True

        tenant_id = view.kwargs.get("tenant_id")
        if not tenant_id:
            return False

        if _est_admin_organisme(request.user, tenant_id):
            return True

        return MembreTenant.objects.filter(
            utilisateur=request.user,
            tenant_id=tenant_id,
            role=MembreTenant.Role.APPRENANT,
            actif=True,
        ).exists()
