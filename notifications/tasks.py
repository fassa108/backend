"""
Tâches planifiées par Celery beat (CELERY_BEAT_SCHEDULE) :
- toutes les heures : les rappels d'échéance des briefs ;
- chaque jour : les alertes de décrochage, les échéances d'abonnement, puis le
  ménage des notifications lues.
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
    from tenants.abonnements import verifier_abonnements

    return {
        "decrochage": alerter_formateurs(),
        # Rappels d'échéance, puis suspension des organismes dont la grâce est passée
        "abonnements": verifier_abonnements(),
        "purgees": purger_notifications_lues(),
    }
