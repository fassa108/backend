"""
Tâche quotidienne lancée par Celery beat (CELERY_BEAT_SCHEDULE).

Les règles d'alerte et de rappel viendront s'ajouter ici ; pour l'instant
elle fait le ménage des notifications lues anciennes.
"""

from celery import shared_task

from .services import purger_notifications_lues


@shared_task
def verifier_alertes():
    return {"purgees": purger_notifications_lues()}
