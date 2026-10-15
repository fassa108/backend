"""
Tâche quotidienne lancée par Celery beat (CELERY_BEAT_SCHEDULE) :
les alertes recalculées chaque jour, puis le ménage des notifications lues.
"""

from celery import shared_task

from .services import purger_notifications_lues


@shared_task
def verifier_alertes():
    from activites.decrochage import alerter_formateurs

    return {
        "decrochage": alerter_formateurs(),
        "purgees": purger_notifications_lues(),
    }
