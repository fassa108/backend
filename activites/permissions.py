from rest_framework.permissions import BasePermission

from accounts.models import MembreTenant


def _est_membre_actif(user, tenant_id, role):
    return MembreTenant.objects.filter(
        utilisateur=user,
        tenant_id=tenant_id,
        role=role,
        actif=True,
    ).exists()


def _est_formateur_de_promotion(user, promotion_id):
    from pedagogie.models import FormateurPromotion
    return FormateurPromotion.objects.filter(
        formateur=user,
        promotion_id=promotion_id,
    ).exists()


class CanConsultBrief(BasePermission):
    """
    Consultation des briefs : APPRENANT, FORMATEUR, ADMINISTRATEUR du tenant.
    Admin SaaS exclu (pas d'accès aux opérations métier organisme).
    """

    message = "Vous n'avez pas accès aux briefs de cet organisme."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        # Admin SaaS exclu des opérations métier organisme
        if request.user.est_admin_saas:
            return False

        tenant_id = view.kwargs.get("tenant_id")
        if not tenant_id:
            return False

        return MembreTenant.objects.filter(
            utilisateur=request.user,
            tenant_id=tenant_id,
            role__in=[
                MembreTenant.Role.APPRENANT,
                MembreTenant.Role.FORMATEUR,
                MembreTenant.Role.ADMINISTRATEUR,
            ],
            actif=True,
        ).exists()


class CanManageBrief(BasePermission):
    """
    Gestion des briefs : FORMATEUR du tenant, limité à ses promotions affectées.
    La restriction de promotion est vérifiée dans le serializer et la view.
    Admin SaaS exclu.
    """

    message = "Seul un formateur peut gérer les briefs."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.est_admin_saas:
            return False

        tenant_id = view.kwargs.get("tenant_id")
        if not tenant_id:
            return False

        return _est_membre_actif(request.user, tenant_id, MembreTenant.Role.FORMATEUR)


class CanConsultRessourceBrief(BasePermission):
    """Consultation des ressources : tout membre actif du tenant. Admin SaaS exclu."""

    message = "Vous n'avez pas accès aux ressources de cet organisme."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.est_admin_saas:
            return False

        tenant_id = view.kwargs.get("tenant_id")
        if not tenant_id:
            return False

        return MembreTenant.objects.filter(
            utilisateur=request.user,
            tenant_id=tenant_id,
            role__in=[
                MembreTenant.Role.APPRENANT,
                MembreTenant.Role.FORMATEUR,
                MembreTenant.Role.ADMINISTRATEUR,
            ],
            actif=True,
        ).exists()


class CanManageRessourceBrief(BasePermission):
    """Gestion des ressources : FORMATEUR uniquement. Admin SaaS exclu."""

    message = "Seul un formateur peut gérer les ressources des briefs."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.est_admin_saas:
            return False

        tenant_id = view.kwargs.get("tenant_id")
        if not tenant_id:
            return False

        return _est_membre_actif(request.user, tenant_id, MembreTenant.Role.FORMATEUR)


class CanConsultAssignation(BasePermission):
    """Consultation des assignations : tout membre actif du tenant. Admin SaaS exclu."""

    message = "Vous n'avez pas accès aux assignations de cet organisme."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.est_admin_saas:
            return False

        tenant_id = view.kwargs.get("tenant_id")
        if not tenant_id:
            return False

        return MembreTenant.objects.filter(
            utilisateur=request.user,
            tenant_id=tenant_id,
            role__in=[
                MembreTenant.Role.APPRENANT,
                MembreTenant.Role.FORMATEUR,
                MembreTenant.Role.ADMINISTRATEUR,
            ],
            actif=True,
        ).exists()


class CanManageAssignation(BasePermission):
    """
    Gestion des assignations : FORMATEUR uniquement.
    La restriction de promotion est vérifiée dans le serializer.
    Admin SaaS exclu.
    """

    message = "Seul un formateur peut gérer les assignations."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.est_admin_saas:
            return False

        tenant_id = view.kwargs.get("tenant_id")
        if not tenant_id:
            return False

        return _est_membre_actif(request.user, tenant_id, MembreTenant.Role.FORMATEUR)


class CanConsultLivrable(BasePermission):
    """
    Consultation des livrables : tout membre actif du tenant.
    Le filtrage par promotion affectée se fait dans le queryset.
    Admin SaaS exclu.
    """

    message = "Vous n'avez pas accès aux livrables de cet organisme."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.est_admin_saas:
            return False

        tenant_id = view.kwargs.get("tenant_id")
        if not tenant_id:
            return False

        return MembreTenant.objects.filter(
            utilisateur=request.user,
            tenant_id=tenant_id,
            role__in=[
                MembreTenant.Role.APPRENANT,
                MembreTenant.Role.FORMATEUR,
                MembreTenant.Role.ADMINISTRATEUR,
            ],
            actif=True,
        ).exists()


class CanCreateLivrable(BasePermission):
    """Dépôt de livrable : APPRENANT uniquement. Admin SaaS exclu."""

    message = "Seul un apprenant autorisé peut déposer un livrable."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.est_admin_saas:
            return False

        tenant_id = view.kwargs.get("tenant_id")
        if not tenant_id:
            return False

        return _est_membre_actif(request.user, tenant_id, MembreTenant.Role.APPRENANT)


class CanUpdateLivrableStatut(BasePermission):
    """Modification du statut d'un livrable : FORMATEUR uniquement. Admin SaaS exclu."""

    message = "Seul un formateur autorisé peut modifier le statut d'un livrable."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.est_admin_saas:
            return False

        tenant_id = view.kwargs.get("tenant_id")
        if not tenant_id:
            return False

        return _est_membre_actif(request.user, tenant_id, MembreTenant.Role.FORMATEUR)


class CanManageRessource(BasePermission):
    """
    Gestion des Ressources indépendantes.
    FORMATEUR (créateur) ou ADMINISTRATEUR du tenant.
    Admin SaaS exclu.
    """

    message = "Vous n'avez pas les droits pour gérer les ressources de cet organisme."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.est_admin_saas:
            return False

        tenant_id = view.kwargs.get("tenant_id")
        if not tenant_id:
            return False

        return MembreTenant.objects.filter(
            utilisateur=request.user,
            tenant_id=tenant_id,
            role__in=[
                MembreTenant.Role.FORMATEUR,
                MembreTenant.Role.ADMINISTRATEUR,
            ],
            actif=True,
        ).exists()


class CanConsultRessource(BasePermission):
    """Consultation des Ressources indépendantes : tout membre actif. Admin SaaS exclu."""

    message = "Vous n'avez pas accès aux ressources de cet organisme."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.est_admin_saas:
            return False

        tenant_id = view.kwargs.get("tenant_id")
        if not tenant_id:
            return False

        return MembreTenant.objects.filter(
            utilisateur=request.user,
            tenant_id=tenant_id,
            role__in=[
                MembreTenant.Role.APPRENANT,
                MembreTenant.Role.FORMATEUR,
                MembreTenant.Role.ADMINISTRATEUR,
            ],
            actif=True,
        ).exists()
