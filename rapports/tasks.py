"""
Génération du texte d'un rapport (Celery) : la demande pseudonymisée part
au service IA (seul Celery le joint), le texte revient en brouillon, et
l'auteur est prévenu. Service injoignable : trois nouvelles tentatives,
puis échec.
"""

import logging

import requests
from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)

MESSAGE_ECHEC = "La génération du rapport a échoué. Réessayez dans quelques instants."


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def generer_rapport(self, pk):
    from notifications.models import Notification
    from notifications.services import notifier

    from .donnees import demande_ia
    from .models import Rapport
    from .texte import markdown

    rapport = Rapport.objects.select_related("promotion__formation", "apprenant").filter(
        pk=pk, statut=Rapport.Statut.EN_COURS
    ).first()
    # Supprimé entre-temps, ou déjà traité
    if rapport is None:
        return

    def terminer(statut, contenu="", erreur=""):
        rapport.statut = statut
        rapport.contenu = contenu
        rapport.erreur = erreur
        rapport.save()
        if rapport.auteur_id:
            pret = statut == Rapport.Statut.BROUILLON
            cible = rapport.donnees.get("apprenant", {}).get("nom") or rapport.promotion.nom
            notifier(
                [rapport.auteur_id],
                Notification.Type.IA,
                f"{rapport.get_type_display()} {'prêt' if pret else 'en échec'} : {cible}",
                "Relisez le brouillon avant de le valider." if pret else erreur,
                f"/rapports/{rapport.id}",
                tenant=rapport.promotion.formation.tenant_id,
            )

    try:
        reponse = requests.post(
            f"{settings.IA_URL}/generate/rapport",
            json=demande_ia(rapport),
            headers={"X-Service-Token": settings.IA_SERVICE_TOKEN},
            timeout=(5, 300),
        )
    except requests.RequestException as erreur:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=erreur)
        logger.warning("Service IA injoignable pour le rapport #%s : %s", pk, erreur)
        return terminer(Rapport.Statut.ECHEC, erreur=MESSAGE_ECHEC)

    if reponse.status_code != 200:
        logger.warning("Rapport #%s refusé par le service IA : %s %s", pk, reponse.status_code, reponse.text[:300])
        return terminer(Rapport.Statut.ECHEC, erreur=MESSAGE_ECHEC)

    terminer(Rapport.Statut.BROUILLON, contenu=markdown(rapport, reponse.json()))
