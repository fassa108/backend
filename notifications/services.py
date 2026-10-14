"""
Création et ménage des notifications.

notifier() est le seul point d'entrée : chaque app l'appelle au moment
où elle programme son email (ou sans email, ex. compétence validée).
"""

from datetime import timedelta

from django.utils import timezone

from .models import Notification

# Les notifications lues sont effacées après ce délai
CONSERVATION_LUES_JOURS = 60


def _id(objet):
    return getattr(objet, "pk", objet)


def notifier(utilisateurs, type, titre, message="", lien="", tenant=None, cle=""):
    """
    Une notification par destinataire (utilisateurs ou identifiants).

    Avec une clé, un destinataire déjà notifié sous cette clé est ignoré.
    Renvoie les identifiants des destinataires effectivement notifiés.
    """
    ids = {_id(u) for u in utilisateurs if u is not None}
    if cle:
        ids -= set(
            Notification.objects.filter(utilisateur_id__in=ids, cle=cle)
            .values_list("utilisateur_id", flat=True)
        )
    if not ids:
        return []
    Notification.objects.bulk_create(
        [
            Notification(
                utilisateur_id=i,
                tenant_id=_id(tenant),
                type=type,
                titre=titre[:150],
                message=message[:500],
                lien=lien,
                cle=cle,
            )
            for i in sorted(ids)
        ],
        # Une alerte calculée en parallèle ne crée pas de doublon
        ignore_conflicts=bool(cle),
    )
    return sorted(ids)


def purger_notifications_lues(jours=CONSERVATION_LUES_JOURS):
    limite = timezone.now() - timedelta(days=jours)
    supprimees, _ = Notification.objects.filter(lue=True, date_lecture__lt=limite).delete()
    return supprimees
