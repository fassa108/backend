"""
Consultation des fichiers dans la plateforme (ressources et livrables).

- PDF : renvoyé tel quel, affiché par le navigateur.
- TXT : renvoyé en texte UTF-8.
- DOCX, PPTX : le navigateur ne sait pas les lire. Au dépôt, Celery les
  envoie à Gotenberg (conteneur isolé, sans accès réseau sortant) qui les
  convertit en PDF ; c'est cet aperçu qui est affiché. L'original reste
  téléchargeable.

La conversion est faite une seule fois, en arrière-plan : le dépôt n'attend
pas. APERCU_OFFICE_ACTIF=0 coupe la conversion.
"""

from django.conf import settings
from django.db import transaction
from django.http import FileResponse, HttpResponse
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from .models import StatutApercu

EXTENSIONS_A_CONVERTIR = {"docx", "pptx"}


def demander_apercu(objet):
    """
    Lance la conversion d'un fichier Office en PDF (après la transaction).
    Sans effet pour un lien, un PDF, un TXT, ou si la conversion est coupée.
    """
    from .tasks import generer_apercu  # import local : tasks importe ce module

    if objet.extension not in EXTENSIONS_A_CONVERTIR or not settings.APERCU_OFFICE_ACTIF:
        return
    objet.apercu_statut = StatutApercu.EN_COURS
    objet.save(update_fields=["apercu_statut"])
    modele = objet._meta.label_lower
    transaction.on_commit(lambda: generer_apercu.delay(modele, objet.pk))


def supprimer_fichiers(objet):
    """Supprime du disque le fichier et son aperçu (suppression ou remplacement)."""
    for champ in (objet.fichier, objet.apercu):
        if champ:
            champ.delete(save=False)


def _texte_utf8(fichier):
    # La validation au dépôt garantit de l'UTF-8 ou du Windows-1252
    brut = fichier.read()
    try:
        return brut.decode("utf-8")
    except UnicodeDecodeError:
        return brut.decode("cp1252", errors="replace")


def reponse_consultation(objet):
    """
    Réponse de la route « consulter » : le contenu à afficher (inline), ou
    l'état de l'aperçu s'il n'est pas prêt.

    - 200 : PDF (original ou aperçu) ou texte
    - 202 : {"apercu": "EN_COURS"} — aperçu en préparation, réessayer plus tard
    - 404 : {"apercu": "ECHEC" | "INDISPONIBLE"} — seul le téléchargement reste
    """
    if not objet.fichier:
        raise NotFound("Cet élément est un lien, pas un fichier.")

    nom = objet.nom_affiche
    ext = objet.extension
    try:
        if ext == "pdf":
            return FileResponse(objet.fichier.open("rb"), content_type="application/pdf", filename=nom)
        if ext == "txt":
            with objet.fichier.open("rb") as fichier:
                return HttpResponse(_texte_utf8(fichier), content_type="text/plain; charset=utf-8")
    except FileNotFoundError:
        raise NotFound("Le fichier est introuvable.")

    # Fichier Office : on affiche son aperçu PDF
    if objet.apercu_statut == StatutApercu.PRET and objet.apercu:
        try:
            return FileResponse(objet.apercu.open("rb"), content_type="application/pdf", filename=f"{nom}.pdf")
        except FileNotFoundError:
            pass  # aperçu perdu : on le régénère ci-dessous

    if objet.apercu_statut == StatutApercu.ECHEC:
        return Response(
            {"apercu": "ECHEC", "detail": "L'aperçu de ce fichier n'a pas pu être généré."},
            status=status.HTTP_404_NOT_FOUND,
        )
    if objet.apercu_statut != StatutApercu.EN_COURS:
        # Fichier déposé avant la mise en place des aperçus, ou aperçu perdu
        if not settings.APERCU_OFFICE_ACTIF:
            return Response(
                {"apercu": "INDISPONIBLE", "detail": "L'aperçu n'est pas disponible pour ce fichier."},
                status=status.HTTP_404_NOT_FOUND,
            )
        demander_apercu(objet)
    return Response(
        {"apercu": "EN_COURS", "detail": "Aperçu en préparation, réessayez dans quelques secondes."},
        status=status.HTTP_202_ACCEPTED,
    )
