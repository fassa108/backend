"""
Validation des fichiers déposés (ressources et livrables).

L'extension ne suffit pas : un fichier renommé en « .pdf » serait accepté.
On vérifie donc aussi le contenu réel (signature du format).
"""

import zipfile

from rest_framework import serializers

# Taille maximale d'un fichier déposé
TAILLE_MAX_FICHIER = 10 * 1024 * 1024  # 10 Mo

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
    if fichier.size > TAILLE_MAX_FICHIER:
        raise serializers.ValidationError("Le fichier ne doit pas dépasser 10 Mo.")

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
