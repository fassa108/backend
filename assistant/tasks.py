"""
Réponse de l'assistant (Celery) : la question et son contexte partent au
service IA (seul Celery le joint). Service injoignable : deux nouvelles
tentatives rapprochées, puis échec affiché dans la conversation.
"""

import logging

import requests
from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)

MESSAGE_ECHEC = "L'assistant n'est pas disponible pour le moment. Réessayez dans quelques instants."


@shared_task(bind=True, max_retries=2, default_retry_delay=5)
def repondre(self, reponse_id):
    from .contexte import demande
    from .models import Message

    reponse = Message.objects.select_related("conversation").filter(
        pk=reponse_id, statut=Message.Statut.EN_COURS
    ).first()
    # Conversation supprimée entre-temps, ou déjà traitée
    if reponse is None:
        return
    question = (
        reponse.conversation.messages.filter(role=Message.Role.APPRENANT, id__lt=reponse.id).order_by("-id").first()
    )

    def terminer(statut, contenu):
        reponse.statut = statut
        reponse.contenu = contenu
        reponse.save(update_fields=["statut", "contenu"])

    if question is None:
        return terminer(Message.Statut.ECHEC, MESSAGE_ECHEC)

    try:
        resultat = requests.post(
            f"{settings.IA_URL}/chat",
            json=demande(question),
            headers={"X-Service-Token": settings.IA_SERVICE_TOKEN},
            timeout=(5, 120),
        )
    except requests.RequestException as erreur:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=erreur)
        logger.warning("Service IA injoignable pour le message #%s : %s", reponse_id, erreur)
        return terminer(Message.Statut.ECHEC, MESSAGE_ECHEC)

    if resultat.status_code != 200:
        logger.warning("Réponse refusée pour le message #%s : %s %s", reponse_id, resultat.status_code, resultat.text[:300])
        return terminer(Message.Statut.ECHEC, MESSAGE_ECHEC)

    terminer(Message.Statut.PRET, resultat.json()["reponse"])
