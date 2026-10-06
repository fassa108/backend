"""
Tâches de fond des activités (Celery).

- Emails : un email par destinataire, les adresses des autres ne sont
  jamais visibles.
- Aperçus : conversion des fichiers Office en PDF par Gotenberg.
"""

import json
import logging

import requests
from celery import shared_task
from django.apps import apps
from django.conf import settings
from django.core.files.base import ContentFile

from config.emails import envoyer

logger = logging.getLogger(__name__)


@shared_task
def envoyer_email_assignation(destinataires, titre_brief, date_limite, url):
    envoyer(
        destinataires,
        sujet=f"Nouveau brief : {titre_brief}",
        titre="Un nouveau brief vous est assigné",
        lignes=[f"Brief : {titre_brief}", f"À rendre avant le {date_limite}."],
        libelle_bouton="Voir le brief",
        url=url,
    )


@shared_task
def envoyer_email_soumission(destinataires, deposant, cible, titre_brief, numero, date_depot, en_retard, url):
    lignes = [
        f"{deposant} a déposé sur le brief « {titre_brief} »"
        + (f" pour {cible}." if cible != deposant else "."),
        f"Dépôt n°{numero}, le {date_depot}.",
    ]
    if en_retard:
        lignes.append("Ce dépôt a été fait après la date limite.")
    envoyer(
        destinataires,
        sujet=f"Nouveau dépôt : {titre_brief}",
        titre="Nouveau dépôt de livrable",
        lignes=lignes,
        libelle_bouton="Voir le brief",
        url=url,
    )


@shared_task
def envoyer_email_evaluation(destinataires, titre_brief, evaluateur, nb_acquises, nb_visees, url):
    lignes = [f"{evaluateur} a évalué votre rendu du brief « {titre_brief} »."]
    if nb_visees:
        lignes.append(f"Compétences acquises : {nb_acquises} sur {nb_visees}.")
    lignes.append("Retrouvez le détail et les commentaires dans votre activité.")
    envoyer(
        destinataires,
        sujet=f"Évaluation : {titre_brief}",
        titre="Votre rendu a été évalué",
        lignes=lignes,
        libelle_bouton="Voir l'évaluation",
        url=url,
    )


@shared_task
def envoyer_email_commentaire(destinataires, auteur, titre_brief, reponse, url):
    if reponse:
        lignes = [f"{auteur} a répondu à un commentaire sur le brief « {titre_brief} »."]
    else:
        lignes = [f"{auteur} a commenté votre rendu du brief « {titre_brief} »."]
    envoyer(
        destinataires,
        sujet=f"Nouveau commentaire : {titre_brief}",
        titre="Nouveau commentaire",
        lignes=lignes,
        libelle_bouton="Lire le commentaire",
        url=url,
    )


# ─── Aperçus des fichiers Office ─────────────────────────────────────────────

MODELES_AVEC_APERCU = {"activites.ressource", "activites.fichierlivrable"}
TYPES_OFFICE = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def generer_apercu(self, modele, pk):
    """
    Convertit le fichier Office d'une ressource ou d'un élément de dépôt en
    PDF (via Gotenberg) et l'enregistre comme aperçu. Gotenberg injoignable :
    trois nouvelles tentatives, puis échec.
    """
    from .models import StatutApercu

    if modele not in MODELES_AVEC_APERCU:
        return
    objet = apps.get_model(modele).objects.filter(pk=pk).first()
    # Supprimé ou déjà traité entre-temps
    if objet is None or objet.apercu_statut != StatutApercu.EN_COURS or objet.extension not in TYPES_OFFICE:
        return

    def echec(raison):
        logger.warning("Aperçu impossible pour %s #%s : %s", modele, pk, raison)
        objet.apercu_statut = StatutApercu.ECHEC
        objet.save(update_fields=["apercu_statut"])

    try:
        with objet.fichier.open("rb") as fichier:
            reponse = requests.post(
                f"{settings.GOTENBERG_URL}/forms/libreoffice/convert",
                files={"files": (f"document.{objet.extension}", fichier, TYPES_OFFICE[objet.extension])},
                # Titre du PDF : affiché par la visionneuse du navigateur
                data={"metadata": json.dumps({"Title": objet.nom_affiche})},
                timeout=(5, 120),
            )
    except FileNotFoundError:
        return echec("fichier introuvable")
    except requests.RequestException as erreur:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=erreur)
        return echec(erreur)

    if reponse.status_code != 200:
        return echec(f"réponse {reponse.status_code}")
    if not reponse.content.startswith(b"%PDF-"):
        return echec("la réponse n'est pas un PDF")

    if objet.apercu:
        objet.apercu.delete(save=False)
    objet.apercu.save("apercu.pdf", ContentFile(reponse.content), save=False)
    objet.apercu_statut = StatutApercu.PRET
    objet.save(update_fields=["apercu", "apercu_statut"])
