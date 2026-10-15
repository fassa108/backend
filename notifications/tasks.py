"""
Tâches planifiées par Celery beat (CELERY_BEAT_SCHEDULE) :
- toutes les heures : les rappels d'échéance des briefs ;
- chaque jour : les alertes de décrochage, puis le ménage des notifications lues.
"""

from celery import shared_task

from .services import purger_notifications_lues


@shared_task
def envoyer_rappels():
    from activites.rappels import envoyer_rappels as envoyer

    return {"rappels": envoyer()}


@shared_task
def verifier_alertes():
    from activites.decrochage import alerter_formateurs

    return {
        "decrochage": alerter_formateurs(),
        "purgees": purger_notifications_lues(),
    }
