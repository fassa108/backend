"""
Texte d'un rapport : markdown rédigé à partir de la réponse de l'IA (les
noms remis à la place des « Apprenant N »), puis HTML sûr pour le PDF
(même mise en forme que le front, utils/miseEnForme.js).
"""

import html
import re

# « l'Apprenant 3 » devient « Awa Ndiaye » : l'article part avec la référence
REFERENCE = re.compile(r"(?:\b[lL]['’]\s?)?\b(Apprenant \d+)\b")

TITRES = {
    "PROMOTION": ("Points forts", "Points de vigilance"),
    "APPRENANT": ("Acquis", "Axes de progrès"),
}


def _noms(rapport):
    return {a["reference"]: a["nom"] for a in rapport.donnees.get("apprenants", [])}


def _remettre_noms(texte, noms):
    return REFERENCE.sub(lambda m: noms.get(m.group(1), m.group(0)), texte)


def _une_ligne(texte):
    return " ".join(str(texte).split())


def markdown(rapport, genere):
    """Markdown du rapport à partir de la réponse de l'IA (RapportGenere)."""
    noms = _noms(rapport)
    forts, vigilance = TITRES[rapport.type]

    def puces(elements):
        return [f"- {_une_ligne(e)}" for e in elements if str(e).strip()]

    blocs = ["## Synthèse", _une_ligne(genere["synthese"])]
    if genere.get("points_forts"):
        blocs += [f"## {forts}", "\n".join(puces(genere["points_forts"]))]
    if genere.get("points_vigilance"):
        blocs += [f"## {vigilance}", "\n".join(puces(genere["points_vigilance"]))]

    if rapport.type == "PROMOTION":
        # Seuls les apprenants de la promotion (référence connue)
        a_suivre = [a for a in genere.get("apprenants_a_suivre", []) if a.get("apprenant") in noms]
        if a_suivre:
            blocs += ["## Apprenants à suivre", "\n".join(
                f"- **{a['apprenant']}** : {_une_ligne(a['constat'])} Action : {_une_ligne(a['action'])}"
                for a in a_suivre
            )]

    if genere.get("recommandations"):
        blocs += ["## Recommandations", "\n".join(
            f"{i}. {_une_ligne(r)}" for i, r in enumerate(genere["recommandations"], start=1)
        )]
    return _remettre_noms("\n\n".join(blocs), noms)


# ─── Markdown → HTML (PDF) ────────────────────────────────────────────────────

def _en_ligne(texte):
    texte = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", texte)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", texte)


def en_html(brut):
    """Texte échappé d'abord : seuls titres, gras, code, listes et paragraphes sont reconstitués."""
    blocs, liste = [], None

    def fermer_liste():
        nonlocal liste
        if liste:
            debut = f' start="{liste["debut"]}"' if liste["debut"] > 1 else ""
            blocs.append(f"<{liste['type']}{debut}>{''.join(liste['items'])}</{liste['type']}>")
        liste = None

    for ligne in html.escape(brut or "").split("\n"):
        titre = re.match(r"^\s*#{1,4}\s+(.*)$", ligne)
        if titre:
            fermer_liste()
            blocs.append(f"<h2>{_en_ligne(titre.group(1))}</h2>")
            continue
        puce = re.match(r"^\s*[-*•]\s+(.*)$", ligne)
        numero = re.match(r"^\s*(\d+)[.)]\s+(.*)$", ligne)
        if puce or numero:
            type_ = "ul" if puce else "ol"
            if not liste or liste["type"] != type_:
                fermer_liste()
                liste = {"type": type_, "items": [], "debut": int(numero.group(1)) if numero else 1}
            liste["items"].append(f"<li>{_en_ligne(puce.group(1) if puce else numero.group(2))}</li>")
        elif not ligne.strip():
            fermer_liste()
        else:
            fermer_liste()
            blocs.append(f"<p>{_en_ligne(ligne)}</p>")
    fermer_liste()
    return "".join(blocs)
