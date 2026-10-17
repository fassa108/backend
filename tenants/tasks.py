from celery import shared_task
from django.conf import settings

from config.emails import envoyer


@shared_task
def envoyer_email_nouvel_organisme(emails_admins, nom_organisme, responsable, montant):
    envoyer(
        emails_admins,
        sujet=f"Nouvel organisme inscrit : {nom_organisme}",
        titre="Nouvel organisme inscrit",
        lignes=[
            f"{responsable} a inscrit l'organisme « {nom_organisme} » et réglé son abonnement ({montant} FCFA).",
            "L'organisme est créé et son administrateur a reçu son email d'activation.",
        ],
        libelle_bouton="Voir les inscriptions",
        url=f"{settings.FRONTEND_URL}/admin/demandes",
    )


@shared_task
def envoyer_email_organisme_cree(email, nom_organisme):
    # Responsable déjà inscrit : pas d'activation, il se connecte directement.
    envoyer(
        [email],
        sujet=f"Votre organisme {nom_organisme} est créé sur EduHub",
        titre="Votre organisme est prêt",
        lignes=[
            f"Votre paiement est confirmé : l'organisme « {nom_organisme} » a été créé, et vous en êtes l'administrateur.",
            "Connectez-vous avec votre compte EduHub habituel pour le configurer et inviter vos équipes.",
        ],
        libelle_bouton="Se connecter",
        url=f"{settings.FRONTEND_URL}/login",
    )


@shared_task
def envoyer_email_lien_paiement(email, prenom, nom_organisme, reference, montant):
    # Au dépôt du formulaire : le responsable peut revenir sur sa page de paiement
    # (payer plus tard, ou vérifier un paiement si la page PayDunya a été fermée).
    envoyer(
        [email],
        sujet=f"Finalisez l'inscription de {nom_organisme} sur EduHub",
        titre="Finalisez votre inscription",
        lignes=[
            f"Bonjour {prenom}, l'inscription de l'organisme « {nom_organisme} » est enregistrée.",
            f"Il reste à régler l'abonnement ({montant} FCFA par mois) pour créer votre espace.",
            "Gardez ce lien : il vous permet aussi de vérifier l'état de votre paiement à tout moment.",
        ],
        libelle_bouton="Accéder au paiement",
        url=f"{settings.FRONTEND_URL}/inscription/paiement/{reference}",
        lien_secours=True,
    )


@shared_task
def envoyer_email_paiement_confirme(email, nom_organisme, montant, reference_transaction, url_recu, reference):
    # Preuve de paiement pour le responsable (en plus de l'email d'activation)
    envoyer(
        [email],
        sujet=f"Paiement confirmé : abonnement EduHub de {nom_organisme}",
        titre="Paiement confirmé",
        lignes=[
            f"Nous avons bien reçu votre paiement de {montant} FCFA pour l'abonnement de « {nom_organisme} ».",
            f"Référence de la transaction : {reference_transaction}",
            "Votre espace est créé : suivez l'email d'activation (ou connectez-vous si vous avez déjà un compte).",
        ],
        libelle_bouton="Voir le reçu" if url_recu else "Voir mon paiement",
        url=url_recu or f"{settings.FRONTEND_URL}/inscription/paiement/{reference}",
    )


@shared_task
def envoyer_email_abonnement(emails, nom_organisme, titre, message):
    # Rappel d'échéance ou suspension, aux administrateurs de l'organisme
    envoyer(
        emails,
        sujet=f"{titre} : {nom_organisme}",
        titre=titre,
        lignes=[f"Organisme : {nom_organisme}", message],
        libelle_bouton="Renouveler l'abonnement",
        url=f"{settings.FRONTEND_URL}/abonnement",
    )


@shared_task
def envoyer_email_renouvellement_confirme(emails, nom_organisme, montant, reference_transaction, url_recu, date_fin):
    envoyer(
        emails,
        sujet=f"Abonnement renouvelé : {nom_organisme}",
        titre="Abonnement renouvelé",
        lignes=[
            f"Nous avons bien reçu votre paiement de {montant} FCFA pour l'abonnement de « {nom_organisme} ».",
            f"Votre abonnement est valable jusqu'au {date_fin}.",
            f"Référence de la transaction : {reference_transaction}",
        ],
        libelle_bouton="Voir le reçu" if url_recu else "Mon abonnement",
        url=url_recu or f"{settings.FRONTEND_URL}/abonnement",
    )
