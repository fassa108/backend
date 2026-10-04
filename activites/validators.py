"""
Validation des fichiers déposés (ressources et livrables).

L'extension ne suffit pas : un fichier renommé en « .pdf » serait accepté.
On vérifie donc aussi le contenu réel (signature du format).
"""

import zipfile

from django.conf import settings
from rest_framework import serializers

# Documents Office : archive ZIP contenant ce dossier
DOSSIER_OFFICE = {
    "docx": "word/",
    "pptx": "ppt/",
}

ERREUR_CONTENU = "Le contenu du fichier ne correspond pas à son extension (.{ext})."


def _lire_debut(fichier, taille):
    fichier.seek(0)
    debut = fichier.read(taille)
    fichier.seek(0)
    return debut


def _est_pdf(fichier):
    return _lire_debut(fichier, 5) == b"%PDF-"


def _est_office(fichier, dossier):
    fichier.seek(0)
    try:
        with zipfile.ZipFile(fichier) as archive:
            noms = archive.namelist()
    except zipfile.BadZipFile:
        return False
    finally:
        fichier.seek(0)
    return "[Content_Types].xml" in noms and any(n.startswith(dossier) for n in noms)


def _est_texte(fichier):
    # Un texte ne contient pas d'octet nul et se décode en UTF-8 ou en Windows-1252
    debut = _lire_debut(fichier, 64 * 1024)
    if b"\x00" in debut:
        return False
    for encodage in ("utf-8", "cp1252"):
        try:
            debut.decode(encodage)
            return True
        except UnicodeDecodeError:
            continue
    return False


def valider_fichier(fichier):
    """
    Vérifie la taille et le contenu réel d'un fichier dont l'extension a
    déjà été acceptée (pdf, docx, pptx, txt). Lève une ValidationError DRF.
    """
    # Lue à chaque appel (taille maximale : settings.TAILLE_MAX_FICHIER_MO)
    taille_max_mo = settings.TAILLE_MAX_FICHIER_MO
    if fichier.size > taille_max_mo * 1024 * 1024:
        raise serializers.ValidationError(f"Le fichier ne doit pas dépasser {taille_max_mo} Mo.")

    ext = fichier.name.rsplit(".", 1)[-1].lower() if "." in fichier.name else ""

    if ext == "pdf":
        valide = _est_pdf(fichier)
    elif ext in DOSSIER_OFFICE:
        valide = _est_office(fichier, DOSSIER_OFFICE[ext])
    elif ext == "txt":
        valide = _est_texte(fichier)
    else:
        # L'extension est contrôlée par le FileExtensionValidator du modèle
        valide = True

    if not valide:
        raise serializers.ValidationError(ERREUR_CONTENU.format(ext=ext))
