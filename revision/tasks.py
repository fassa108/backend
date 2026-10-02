"""
Génération des quiz et fiches de révision par le service IA (Celery).

Les fichiers sources sont envoyés au service IA (eduhub_fastapi) avec le
jeton partagé. Service injoignable : trois nouvelles tentatives, puis
échec. Les fichiers ajoutés pour l'occasion sont effacés à la fin.
"""

import logging

import requests
from celery import shared_task
from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)

# Erreurs du service IA dont le message s'adresse au formateur (fichier
# illisible, trop long…) ; les autres sont remplacées par un message général.
CODES_MESSAGE_UTILE = {413, 415, 422}
MESSAGE_GENERAL = "La génération a échoué. Supprimez ce brouillon et réessayez plus tard."


def _sources(support):
    """(nom avec extension, FieldFile) de chaque fichier source."""
    sources = [(r.nom_affiche, r.fichier) for r in support.ressources.all() if r.fichier]
    sources += [(f.nom, f.fichier) for f in support.fichiers_livrables.all() if f.fichier]
    sources += [(f.nom, f.fichier) for f in support.fichiers_ajoutes.all()]
    return sources


def _lire(sources):
    fichiers = []
    for nom, champ in sources:
        with champ.open("rb") as f:
            fichiers.append(("fichiers", (nom, f.read())))
    return fichiers


def _effacer_fichiers_ajoutes(support):
    for source in support.fichiers_ajoutes.all():
        source.fichier.delete(save=False)
    support.fichiers_ajoutes.all().delete()


def _enregistrer(support, resultat):
    from .models import Option, Question

    if support.est_quiz:
        for i, q in enumerate(resultat["questions"], start=1):
            question = Question.objects.create(
                support=support,
                ordre=i,
                intitule=q["intitule"],
                type=q["type"].upper(),
                explication=q.get("explication") or "",
            )
            Option.objects.bulk_create(
                Option(question=question, ordre=j, texte=o["texte"], est_correcte=o["est_correcte"])
                for j, o in enumerate(q["options"], start=1)
            )
        support.titre = support.titre or f"Quiz {support.get_difficulte_display().lower()} : {support.module.nom}"
    else:
        support.contenu = resultat
        support.titre = resultat["titre"][:255]


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def generer_support(self, pk):
    from .models import SupportRevision

    support = SupportRevision.objects.select_related("module").filter(
        pk=pk, statut=SupportRevision.Statut.EN_COURS
    ).first()
    # Supprimé entre-temps, ou déjà traité
    if support is None:
        return

    def terminer(statut, resultat=None, erreur=""):
        with transaction.atomic():
            courant = SupportRevision.objects.select_for_update().filter(
                pk=pk, statut=SupportRevision.Statut.EN_COURS
            ).first()
            if courant is None:
                return
            if resultat is not None:
                _enregistrer(courant, resultat)
            courant.statut = statut
            courant.erreur = erreur
            courant.save()
        _effacer_fichiers_ajoutes(support)

    try:
        fichiers = _lire(_sources(support))
    except FileNotFoundError:
        return terminer(SupportRevision.Statut.ECHEC, erreur="Un fichier source est introuvable.")

    if support.est_quiz:
        url = f"{settings.IA_URL}/generate/quiz"
        donnees = {"nb_questions": support.nb_questions, "difficulte": support.difficulte.lower()}
    else:
        url = f"{settings.IA_URL}/generate/fiche"
        donnees = {}

    try:
        reponse = requests.post(
            url,
            files=fichiers,
            data=donnees,
            headers={"X-Service-Token": settings.IA_SERVICE_TOKEN},
            # Le LLM peut être lent (nouvelles tentatives côté service IA)
            timeout=(5, 300),
        )
    except requests.RequestException as erreur:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=erreur)
        logger.warning("Service IA injoignable pour le support #%s : %s", pk, erreur)
        return terminer(SupportRevision.Statut.ECHEC, erreur=MESSAGE_GENERAL)

    if reponse.status_code != 200:
        try:
            detail = reponse.json().get("detail")
        except ValueError:
            detail = None
        logger.warning("Génération refusée pour le support #%s : %s %s", pk, reponse.status_code, detail)
        message = detail if reponse.status_code in CODES_MESSAGE_UTILE and isinstance(detail, str) else MESSAGE_GENERAL
        return terminer(SupportRevision.Statut.ECHEC, erreur=message)

    terminer(SupportRevision.Statut.BROUILLON, resultat=reponse.json())
