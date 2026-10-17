import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, ValidationError

from accounts.models import MembreTenant, Utilisateur
from accounts.services import AccountService
from notifications.models import Notification
from notifications.services import notifier

from . import paydunya
from .abonnements import abonnement_de, creer_abonnement, prolonger
from .models import Abonnement, DemandeInscription, Paiement, Tenant
from .tasks import (
    envoyer_email_nouvel_organisme,
    envoyer_email_organisme_cree,
    envoyer_email_paiement_confirme,
    envoyer_email_renouvellement_confirme,
)

logger = logging.getLogger(__name__)

# Une facture PayDunya non payée expire au bout de 24 h
DUREE_FACTURE_HEURES = 24


def _mode():
    return Paiement.Mode.LIVE if settings.PAYDUNYA_MODE == "live" else Paiement.Mode.TEST


class ServiceIndisponible(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Service momentanément indisponible."


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
        # Premier mois : payé à l'inscription, ou offert par l'admin SaaS
        creer_abonnement(tenant)

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
    """
    Paiement de l'abonnement via PayDunya :
    1. initier_paiement : facture créée chez PayDunya, le responsable est
       redirigé vers leur page de paiement ;
    2. confirmer_paiement : au retour du navigateur (ou au callback de
       PayDunya), l'état de la facture est relu chez PayDunya ; payée, elle
       crée l'organisme et son premier administrateur (_finaliser).
    """

    MESSAGE_INDISPONIBLE = "Le paiement en ligne est momentanément indisponible. Réessayez dans quelques minutes."

    @staticmethod
    def _verifier_payable(demande):
        if demande.statut == DemandeInscription.Statut.PAYEE:
            raise ValidationError("Cette inscription est déjà réglée.")

        # Un organisme du même nom a pu être créé depuis le dépôt de la demande.
        if Tenant.objects.filter(nom__iexact=demande.nom_organisme).exists():
            raise ValidationError(
                "Un organisme portant ce nom existe déjà. Aucun paiement n'a été effectué : "
                "recommencez l'inscription sous un autre nom."
            )

        if Utilisateur.objects.filter(email__iexact=demande.email, est_admin_saas=True).exists():
            raise ValidationError("Cette adresse ne peut pas administrer un organisme.")

    @staticmethod
    @transaction.atomic
    def initier_paiement(reference):
        """Crée la facture PayDunya et renvoie l'adresse de la page de paiement."""

        demande = DemandeInscription.objects.select_for_update().get(reference=reference)
        DemandeInscriptionService._verifier_payable(demande)

        page = f"{settings.FRONTEND_URL}/inscription/paiement/{demande.reference}"
        client = {"name": f"{demande.responsable_prenom} {demande.responsable_nom}", "email": demande.email}
        if demande.telephone:
            client["phone"] = demande.telephone
        try:
            jeton, url = paydunya.creer_facture(
                montant=settings.PRIX_ABONNEMENT_FCFA,
                description=f"Abonnement mensuel EduHub : {demande.nom_organisme}",
                client=client,
                # PayDunya ajoute « ?token=… » à l'adresse de retour
                return_url=f"{page}/retour",
                cancel_url=f"{page}?annule=1",
                callback_url=f"{settings.BACKEND_URL}/api/demandes-inscription/paydunya/ipn/",
                custom_data={"reference": str(demande.reference)},
            )
        except paydunya.PayDunyaIndisponible:
            raise ServiceIndisponible(DemandeInscriptionService.MESSAGE_INDISPONIBLE)

        Paiement.objects.create(
            type=Paiement.Type.INSCRIPTION,
            demande=demande,
            mode=_mode(),
            montant=settings.PRIX_ABONNEMENT_FCFA,
            reference_transaction=jeton,
            url_paiement=url,
        )
        return url

    @staticmethod
    @transaction.atomic
    def confirmer_paiement(jeton, reference=None, tenant=None):
        """
        Relit la facture chez PayDunya et enregistre son état. Payée :
        - inscription : l'organisme est créé ;
        - renouvellement : l'abonnement est prolongé d'un mois ;
        une seule fois, même si la confirmation arrive deux fois (retour du
        navigateur et callback). « reference » ou « tenant » restreignent
        la recherche à une inscription ou à un organisme. Renvoie le paiement.
        """

        filtre = {"reference_transaction": jeton}
        if reference is not None:
            filtre["demande__reference"] = reference
        if tenant is not None:
            filtre.update(tenant=tenant, type=Paiement.Type.RENOUVELLEMENT)
        paiement = Paiement.objects.select_for_update().filter(**filtre).first()
        if paiement is None:
            raise NotFound("Paiement introuvable.")
        # Verrou sur la demande ou l'abonnement : deux confirmations simultanées
        # ne créent qu'un organisme, ne prolongent qu'une fois
        if paiement.type == Paiement.Type.INSCRIPTION:
            demande = DemandeInscription.objects.select_for_update().get(pk=paiement.demande_id)
            paiement.demande = demande
        else:
            abonnement = Abonnement.objects.select_for_update().select_related("tenant").get(tenant_id=paiement.tenant_id)
        if paiement.statut != Paiement.Statut.EN_ATTENTE:
            return paiement

        # Lu directement chez PayDunya (HTTPS, avec nos clés) : la réponse fait foi
        try:
            facture = paydunya.confirmer(jeton)
        except paydunya.PayDunyaIndisponible:
            raise ServiceIndisponible(DemandeInscriptionService.MESSAGE_INDISPONIBLE)

        etat = facture.get("status")
        if etat in ("cancelled", "failed"):
            paiement.statut = Paiement.Statut.ANNULE if etat == "cancelled" else Paiement.Statut.ECHEC
            paiement.save(update_fields=["statut"])
            return paiement
        if etat != "completed":
            return paiement  # toujours en attente chez PayDunya

        montant = int(float((facture.get("invoice") or {}).get("total_amount") or 0))
        if montant != paiement.montant:
            logger.error("Paiement %s : montant %s au lieu de %s", jeton, montant, paiement.montant)
            paiement.statut = Paiement.Statut.ECHEC
            paiement.save(update_fields=["statut"])
            return paiement

        paiement.statut = Paiement.Statut.REUSSI
        paiement.telephone = ((facture.get("customer") or {}).get("phone") or "")[:30]
        paiement.url_recu = (facture.get("receipt_url") or "")[:500]
        paiement.date_confirmation = timezone.now()
        paiement.save(update_fields=["statut", "telephone", "url_recu", "date_confirmation"])

        if paiement.type == Paiement.Type.RENOUVELLEMENT:
            AbonnementService.finaliser_renouvellement(abonnement, paiement)
            Paiement.objects.filter(
                tenant_id=paiement.tenant_id, type=Paiement.Type.RENOUVELLEMENT, statut=Paiement.Statut.EN_ATTENTE,
            ).update(statut=Paiement.Statut.ANNULE)
        elif demande.statut != DemandeInscription.Statut.PAYEE:
            DemandeInscriptionService._finaliser(demande, paiement)
            # Tentatives abandonnées sur la page PayDunya : sans suite
            demande.paiements.filter(statut=Paiement.Statut.EN_ATTENTE).update(statut=Paiement.Statut.ANNULE)
        else:
            # Deux factures payées pour la même inscription : à rembourser à la main
            logger.error("Inscription %s payée deux fois (%s)", demande.reference, jeton)
        return paiement

    @staticmethod
    def reverifier(demande):
        """
        Page de paiement rouverte (lien reçu par email) : les tentatives
        encore en attente, de moins de 24 h, sont relues chez PayDunya.
        Une page PayDunya fermée avant le retour sur EduHub ne bloque ainsi
        pas l'inscription. PayDunya injoignable : on réessaiera plus tard.
        """
        if demande.statut == DemandeInscription.Statut.PAYEE:
            return
        limite = timezone.now() - timedelta(hours=DUREE_FACTURE_HEURES)
        jetons = demande.paiements.filter(
            statut=Paiement.Statut.EN_ATTENTE, date_paiement__gte=limite
        ).values_list("reference_transaction", flat=True)
        for jeton in list(jetons):
            try:
                paiement = DemandeInscriptionService.confirmer_paiement(jeton)
            except ServiceIndisponible:
                return
            if paiement.statut == Paiement.Statut.REUSSI:
                return

    @staticmethod
    def _finaliser(demande, paiement):
        """
        Paiement reçu : crée l'organisme et son premier administrateur
        (le responsable de la demande) :
        - nouveau compte : il reçoit l'invitation d'activation habituelle ;
        - compte existant : il devient administrateur et en est prévenu.
        Les admins SaaS sont informés de la nouvelle inscription.
        """

        if Tenant.objects.filter(nom__iexact=demande.nom_organisme).exists():
            # Nom pris pendant le paiement : l'admin SaaS régularise
            logger.error("Inscription %s payée, mais le nom « %s » est pris", demande.reference, demande.nom_organisme)
            return

        existant = Utilisateur.objects.filter(email__iexact=demande.email).first()
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
        abonnement = demande.tenant.abonnement
        paiement.tenant = demande.tenant
        paiement.periode_debut, paiement.periode_fin = abonnement.date_debut, abonnement.date_fin
        paiement.save(update_fields=["tenant", "periode_debut", "periode_fin"])

        # Preuve de paiement pour le responsable
        transaction.on_commit(
            lambda: envoyer_email_paiement_confirme.delay(
                demande.email, demande.nom_organisme, paiement.montant,
                paiement.reference_transaction, paiement.url_recu, str(demande.reference),
            )
        )

        # Un compte jamais activé reçoit déjà un lien d'activation.
        if existant and existant.actif:
            transaction.on_commit(
                lambda: envoyer_email_organisme_cree.delay(demande.email, demande.nom_organisme)
            )

        notifier(
            Utilisateur.objects.filter(email__iexact=demande.email),
            Notification.Type.INSCRIPTION,
            "Bienvenue sur EduHub",
            f"Votre organisme « {demande.nom_organisme} » est prêt : commencez par créer une formation.",
            "/dashboard/admin",
            tenant=demande.tenant,
        )

        admins = list(Utilisateur.objects.filter(est_admin_saas=True, actif=True))
        emails_admins = [a.email for a in admins]
        if emails_admins:
            responsable = f"{demande.responsable_prenom} {demande.responsable_nom}"
            notifier(
                admins,
                Notification.Type.INSCRIPTION,
                f"Nouvel organisme : {demande.nom_organisme}",
                f"Inscrit par {responsable}, paiement de {paiement.montant} FCFA reçu.",
                f"/admin/organismes/{demande.tenant.id}",
            )
            transaction.on_commit(
                lambda: envoyer_email_nouvel_organisme.delay(
                    emails_admins, demande.nom_organisme, responsable, paiement.montant,
                )
            )


class AbonnementService:
    """
    Renouvellement mensuel par l'administrateur de l'organisme, via
    PayDunya (même parcours que l'inscription). Possible même quand
    l'organisme est suspendu pour impayé : le paiement lève la suspension.
    """

    @staticmethod
    @transaction.atomic
    def initier_renouvellement(tenant, utilisateur):
        abonnement_de(tenant)
        abonnement = Abonnement.objects.select_for_update().get(tenant=tenant)
        page = f"{settings.FRONTEND_URL}/abonnement"
        try:
            jeton, url = paydunya.creer_facture(
                montant=abonnement.montant_mensuel,
                description=f"Renouvellement mensuel EduHub : {tenant.nom}",
                client={"name": f"{utilisateur.prenom} {utilisateur.nom}", "email": utilisateur.email},
                return_url=f"{page}/retour",
                cancel_url=f"{page}?annule=1",
                callback_url=f"{settings.BACKEND_URL}/api/demandes-inscription/paydunya/ipn/",
                custom_data={"organisme": tenant.id},
            )
        except paydunya.PayDunyaIndisponible:
            raise ServiceIndisponible(DemandeInscriptionService.MESSAGE_INDISPONIBLE)

        Paiement.objects.create(
            type=Paiement.Type.RENOUVELLEMENT,
            tenant=tenant,
            payeur=utilisateur,
            mode=_mode(),
            montant=abonnement.montant_mensuel,
            reference_transaction=jeton,
            url_paiement=url,
        )
        return url

    @staticmethod
    def finaliser_renouvellement(abonnement, paiement):
        """Paiement reçu : un mois de plus, suspension levée, admins prévenus."""
        etait_suspendu = abonnement.suspendu_pour_impaye
        paiement.periode_debut, paiement.periode_fin = prolonger(abonnement)
        paiement.save(update_fields=["periode_debut", "periode_fin"])

        tenant = abonnement.tenant
        admins = list(
            Utilisateur.objects.filter(
                membres_tenants__tenant=tenant,
                membres_tenants__role=MembreTenant.Role.ADMINISTRATEUR,
                membres_tenants__actif=True,
                actif=True,
            )
        )
        fin = f"{abonnement.date_fin:%d/%m/%Y}"
        notifier(
            admins,
            Notification.Type.ABONNEMENT,
            "Abonnement renouvelé",
            f"Valable jusqu'au {fin}." + (" L'accès de tous les membres est rétabli." if etait_suspendu else ""),
            "/abonnement",
            tenant=tenant,
        )
        emails = [a.email for a in admins]
        transaction.on_commit(lambda: envoyer_email_renouvellement_confirme.delay(
            emails, tenant.nom, paiement.montant, paiement.reference_transaction, paiement.url_recu, fin,
        ))
        notifier(
            Utilisateur.objects.filter(est_admin_saas=True, actif=True),
            Notification.Type.ABONNEMENT,
            f"Renouvellement : {tenant.nom}",
            f"{paiement.montant} FCFA reçus, abonnement jusqu'au {fin}.",
            f"/admin/organismes/{tenant.id}",
        )

    @staticmethod
    def reverifier(tenant):
        """Page « Mon abonnement » ouverte : renouvellements en attente (< 24 h) relus chez PayDunya."""
        limite = timezone.now() - timedelta(hours=DUREE_FACTURE_HEURES)
        jetons = Paiement.objects.filter(
            tenant=tenant, type=Paiement.Type.RENOUVELLEMENT,
            statut=Paiement.Statut.EN_ATTENTE, date_paiement__gte=limite,
        ).values_list("reference_transaction", flat=True)
        for jeton in list(jetons):
            try:
                paiement = DemandeInscriptionService.confirmer_paiement(jeton, tenant=tenant)
            except ServiceIndisponible:
                return
            if paiement.statut == Paiement.Statut.REUSSI:
                return
