from celery import shared_task
from django.conf import settings

from config.emails import envoyer


@shared_task
def envoyer_email_reset_password(email, reset_url):
    envoyer(
        [email],
        sujet="Réinitialisation de votre mot de passe",
        titre="Réinitialisation de votre mot de passe",
        lignes=[
            "Vous avez demandé la réinitialisation de votre mot de passe pour votre compte EduHub.",
            "Cliquez sur le bouton ci-dessous pour définir un nouveau mot de passe.",
        ],
        libelle_bouton="Réinitialiser mon mot de passe",
        url=reset_url,
        note=(
            "Ce lien est valable pendant <strong>2 heures</strong>. "
            "Si vous n'êtes pas à l'origine de cette demande, vous pouvez simplement ignorer cet email."
        ),
        lien_secours=True,
    )


@shared_task
def envoyer_email_activation(email, activation_url):
    envoyer(
        [email],
        sujet="Activez votre compte EduHub",
        titre="Bienvenue sur EduHub",
        lignes=[
            "Vous avez été invité à rejoindre EduHub.",
            "Cliquez sur le bouton ci-dessous pour activer votre compte et définir votre mot de passe.",
        ],
        libelle_bouton="Activer mon compte",
        url=activation_url,
        note=f"Ce lien est valable pendant <strong>{settings.DUREE_LIEN_ACTIVATION_HEURES} heures</strong>.",
        lien_secours=True,
    )
