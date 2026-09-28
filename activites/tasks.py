"""
Tâches de fond des activités (Celery).

- Emails : un email par destinataire, les adresses des autres ne sont
  jamais visibles.
- Aperçus : conversion des fichiers Office en PDF par Gotenberg.
"""

import json
import logging
from html import escape

import requests
from celery import shared_task
from django.apps import apps
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMultiAlternatives

logger = logging.getLogger(__name__)


def _mise_en_page(titre, lignes, libelle_bouton, url):
    """HTML simple aux couleurs d'EduHub (les textes sont échappés)."""
    paragraphes = "".join(
        f'<p style="margin:0 0 12px;font-size:15px;line-height:1.6;color:#3f3f46;">{escape(l)}</p>'
        for l in lignes
    )
    return f"""
    <html>
      <body style="margin:0;padding:0;background:#f5f7fa;font-family:Arial,Helvetica,sans-serif;">
        <div style="max-width:600px;margin:40px auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.08);">
          <div style="padding:24px;text-align:center;background:#242424;">
            <h1 style="margin:0;color:#ffffff;font-size:24px;">EduHub</h1>
          </div>
          <div style="padding:32px;">
            <h2 style="margin:0 0 20px;font-size:20px;color:#111827;">{escape(titre)}</h2>
            {paragraphes}
            <div style="margin-top:28px;text-align:center;">
              <a href="{escape(url, quote=True)}" style="display:inline-block;padding:12px 24px;background:#6366f1;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:bold;">
                {escape(libelle_bouton)}
              </a>
            </div>
          </div>
          <div style="padding:16px;text-align:center;border-top:1px solid #eeeeee;">
            <p style="margin:0;font-size:12px;color:#888888;">Cet email a été envoyé automatiquement par EduHub.</p>
          </div>
        </div>
      </body>
    </html>
    """


def _envoyer(destinataires, sujet, titre, lignes, libelle_bouton, url):
    html = _mise_en_page(titre, lignes, libelle_bouton, url)
    texte = "\n\n".join([titre, *lignes, f"{libelle_bouton} : {url}"])
    for email in destinataires:
        message = EmailMultiAlternatives(
            subject=sujet,
            body=texte,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[email],
        )
        message.attach_alternative(html, "text/html")
        message.send()


@shared_task
def envoyer_email_assignation(destinataires, titre_brief, date_limite, url):
    _envoyer(
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
    _envoyer(
        destinataires,
        sujet=f"Nouveau dépôt : {titre_brief}",
        titre="Nouveau dépôt de livrable",
        lignes=lignes,
        libelle_bouton="Voir le brief",
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
