"""
Texte riche des briefs (contexte, modalités, critères, livrables attendus).

Le frontend (Tiptap) envoie du HTML. Il est nettoyé ici, à chaque
enregistrement, sur une liste blanche : on ne fait jamais confiance au
navigateur (l'API peut être appelée directement).
"""

import re
from html import unescape

import nh3

BALISES_AUTORISEES = {
    "p", "br",
    "strong", "b", "em", "i", "u", "s",
    "ul", "ol", "li",
    "h2", "h3",
    "blockquote",
    "a",
}

ATTRIBUTS_AUTORISES = {
    "a": {"href"},
}


def nettoyer_html(html):
    """Retire tout ce qui n'est pas dans la liste blanche (scripts, styles, événements…)."""
    if not html:
        return ""
    return nh3.clean(
        html,
        tags=BALISES_AUTORISEES,
        attributes=ATTRIBUTS_AUTORISES,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer nofollow",
    ).strip()


def est_vide(html):
    """Vrai si le HTML ne contient aucun texte (ex. « <p></p> » d'un éditeur vide)."""
    texte = unescape(re.sub(r"<[^>]*>", "", html or ""))
    return not texte.replace("\xa0", " ").strip()
