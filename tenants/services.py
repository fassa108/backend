from django.db import transaction

from accounts.models import MembreTenant, Utilisateur
from accounts.services import AccountService

from .models import Tenant


class TenantService:

    @staticmethod
    @transaction.atomic
    def creer_organisme(
        *,
        admin_email,
        admin_nom=None,
        admin_prenom=None,
        **champs_tenant
    ):
        """
        Crée un organisme et son premier administrateur.

        - Compte existant : il devient administrateur de l'organisme.
        - Nouveau compte : il est créé inactif et reçoit une invitation.

        L'opération est atomique : si l'ajout de l'administrateur
        échoue, l'organisme n'est pas créé.
        """

        tenant = Tenant.objects.create(**champs_tenant)

        utilisateur = Utilisateur.objects.filter(
            email__iexact=admin_email
        ).first()

        if utilisateur:
            AccountService.ajouter_membre_existant(
                utilisateur=utilisateur,
                tenant=tenant,
                role=MembreTenant.Role.ADMINISTRATEUR,
            )
        else:
            AccountService.inviter_utilisateur(
                nom=admin_nom,
                prenom=admin_prenom,
                email=admin_email,
                tenant=tenant,
                role=MembreTenant.Role.ADMINISTRATEUR,
            )

        return tenant

    @staticmethod
    def est_vide(tenant):
        """
        Un organisme est « vide » s'il n'a aucune donnée pédagogique
        et aucun membre autre que ses administrateurs.
        """

        return not (
            tenant.formations.exists()
            or tenant.niveaux.exists()
            or tenant.ressources.exists()
            or tenant.membres.exclude(
                role=MembreTenant.Role.ADMINISTRATEUR
            ).exists()
        )
