import uuid

from django.conf import settings
from django.db import transaction
from rest_framework.exceptions import ValidationError

from accounts.models import MembreTenant, Utilisateur
from accounts.services import AccountService

from .models import DemandeInscription, Paiement, Tenant
from .tasks import envoyer_email_nouvel_organisme, envoyer_email_organisme_cree


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

        - Compte existant : il devient administrateur de l'organisme ;
          s'il n'a jamais été activé, il reçoit un nouveau lien d'activation.
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
            # Un compte inactif n'a jamais été activé : l'ancien lien
            # a pu expirer, on en envoie un nouveau.
            if not utilisateur.actif:
                AccountService.envoyer_invitation(utilisateur)
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


class DemandeInscriptionService:

    @staticmethod
    @transaction.atomic
    def payer(reference, *, moyen, telephone):
        """
        Paiement simulé de l'abonnement : il réussit toujours.

        Crée le paiement, puis l'organisme et son premier administrateur
        (le responsable de la demande) :
        - nouveau compte : il reçoit l'invitation d'activation habituelle ;
        - compte existant : il devient administrateur et en est prévenu.
        Les admins SaaS sont informés de la nouvelle inscription.
        """

        # Verrou : un double clic ne paie pas deux fois.
        demande = DemandeInscription.objects.select_for_update().get(reference=reference)
        if demande.statut == DemandeInscription.Statut.PAYEE:
            raise ValidationError("Cette inscription est déjà réglée.")

        # Un organisme du même nom a pu être créé depuis le dépôt de la demande.
        if Tenant.objects.filter(nom__iexact=demande.nom_organisme).exists():
            raise ValidationError(
                "Un organisme portant ce nom existe déjà. Aucun paiement n'a été effectué : "
                "recommencez l'inscription sous un autre nom."
            )

        existant = Utilisateur.objects.filter(email__iexact=demande.email).first()
        if existant and existant.est_admin_saas:
            raise ValidationError("Cette adresse ne peut pas administrer un organisme.")

        prefixe = "WAV" if moyen == Paiement.Moyen.WAVE else "OM"
        paiement = Paiement.objects.create(
            demande=demande,
            moyen=moyen,
            telephone=telephone,
            montant=settings.PRIX_ABONNEMENT_FCFA,
            reference_transaction=f"{prefixe}-{uuid.uuid4().hex[:10].upper()}",
        )

        demande.tenant = TenantService.creer_organisme(
            admin_email=demande.email,
            admin_nom=demande.responsable_nom,
            admin_prenom=demande.responsable_prenom,
            nom=demande.nom_organisme,
            email=demande.email,
            telephone=demande.telephone,
        )
        demande.statut = DemandeInscription.Statut.PAYEE
        demande.save(update_fields=["tenant", "statut"])

        # Un compte jamais activé reçoit déjà un lien d'activation.
        if existant and existant.actif:
            transaction.on_commit(
                lambda: envoyer_email_organisme_cree.delay(demande.email, demande.nom_organisme)
            )

        emails_admins = list(
            Utilisateur.objects
            .filter(est_admin_saas=True, actif=True)
            .values_list("email", flat=True)
        )
        if emails_admins:
            responsable = f"{demande.responsable_prenom} {demande.responsable_nom}"
            transaction.on_commit(
                lambda: envoyer_email_nouvel_organisme.delay(
                    emails_admins, demande.nom_organisme, responsable, paiement.montant,
                )
            )

        return demande
