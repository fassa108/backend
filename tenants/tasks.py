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
