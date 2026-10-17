"""
Export PDF d'un rapport (WeasyPrint) : le texte, et les tableaux calculés
par Django au moment de la demande.
"""

import math
from datetime import date

from django.template.loader import render_to_string
from django.utils import timezone

from .texte import en_html

LARGEUR_COURBE, HAUTEUR_COURBE = 640, 150
LARGEUR_HISTO, HAUTEUR_HISTO = 240, 110

# Couleurs des graphiques (les mêmes que la page du rapport)
ETATS = [
    ("VALIDE", "Validés", "#10b981"),
    ("NON_VALIDE", "Non validés", "#ef4444"),
    ("A_EVALUER", "À évaluer", "#0ea5e9"),
    ("NON_RENDU", "Non rendus (échus)", "#f59e0b"),
    ("A_RENDRE", "À rendre", "#cbd5e1"),
]
GENRES = {"F": "#8b5cf6", "H": "#14b8a6", "": "#cbd5e1"}
TRANCHES = ["#f87171", "#fbbf24", "#60a5fa", "#34d399"]

# Annexe du PDF : comment chaque chiffre est calculé
DEFINITIONS = [
    ("Progression", "Part des compétences-niveaux du référentiel de la formation validées par l'apprenant."),
    ("Taux de rendu", "Rendus reçus sur rendus attendus, pour les briefs dont l'échéance est passée."),
    ("Rendu à temps / en retard", "Selon la date du premier dépôt par rapport à l'échéance du brief."),
    ("Rendu manquant", "Brief échu sans aucun dépôt."),
    ("Rendu validé", "Dernière évaluation : toutes les compétences visées par le brief sont acquises."),
    ("Rendu non validé", "Dernière évaluation : au moins une compétence visée n'est pas acquise."),
    ("Taux de validation", "Rendus validés sur rendus évalués (validés et non validés)."),
    ("Délai moyen de correction", "Temps moyen entre un dépôt et son évaluation, réévaluations comprises."),
    ("Apprenant à suivre", "Signalé par la détection du décrochage : retards, inactivité, progression faible."),
    ("Genre", "Renseigné par l'administrateur de l'organisme ; les comptes sans genre sont « non renseignés »."),
]


def _anneau(segments, rayon=38, epaisseur=14):
    """
    Graphique en anneau (SVG) : segments [(libelle, valeur, couleur)].
    Renvoie None si tout est à zéro.
    """
    total = sum(v for _, v, _ in segments)
    if not total:
        return None
    centre = rayon + epaisseur / 2
    arcs, angle = [], -math.pi / 2
    for libelle, valeur, couleur in segments:
        if not valeur:
            continue
        part = valeur / total
        if part >= 0.9999:  # un seul segment : cercle complet
            arcs.append({"cercle": True, "couleur": couleur})
            break
        fin = angle + part * 2 * math.pi
        x1, y1 = centre + rayon * math.cos(angle), centre + rayon * math.sin(angle)
        x2, y2 = centre + rayon * math.cos(fin), centre + rayon * math.sin(fin)
        grand = 1 if part > 0.5 else 0
        arcs.append({
            "chemin": f"M {x1:.2f} {y1:.2f} A {rayon} {rayon} 0 {grand} 1 {x2:.2f} {y2:.2f}",
            "couleur": couleur,
        })
        angle = fin
    return {
        "arcs": arcs,
        "taille": round(2 * centre, 1),
        "centre": round(centre, 1),
        "rayon": rayon,
        "epaisseur": epaisseur,
        "total": total,
        "legende": [
            {"libelle": l, "valeur": v, "pct": round(100 * v / total), "couleur": c}
            for l, v, c in segments if v
        ],
    }


def _histogramme(tranches):
    """Histogramme (SVG) des apprenants par tranche de progression."""
    if not tranches or not any(t["nb"] for t in tranches):
        return None
    maximum = max(t["nb"] for t in tranches)
    largeur = LARGEUR_HISTO / len(tranches)
    barres = []
    for i, t in enumerate(tranches):
        hauteur = round(t["nb"] * (HAUTEUR_HISTO - 16) / maximum, 1)
        barres.append({
            "x": round(i * largeur + largeur * 0.15, 1),
            "y": round(HAUTEUR_HISTO - hauteur, 1),
            "largeur": round(largeur * 0.7, 1),
            "hauteur": hauteur,
            "milieu": round(i * largeur + largeur / 2, 1),
            "y_texte": round(HAUTEUR_HISTO - hauteur - 3, 1),
            "nb": t["nb"],
            "tranche": t["tranche"],
            "couleur": TRANCHES[i % len(TRANCHES)],
        })
    return {"barres": barres, "largeur": LARGEUR_HISTO, "hauteur": HAUTEUR_HISTO}


def _segments_bilan(ligne):
    """Barre empilée d'un brief : part de chaque état parmi les rendus attendus."""
    attendus = ligne["attendus"] or 1
    a_rendre = 0 if ligne["echu"] else ligne["non_rendus"]
    valeurs = {
        "VALIDE": ligne["valides"], "NON_VALIDE": ligne["non_valides"], "A_EVALUER": ligne["a_evaluer"],
        "NON_RENDU": ligne["non_rendus"] - a_rendre, "A_RENDRE": a_rendre,
    }
    return [
        {"pct": round(100 * valeurs[cle] / attendus, 1), "couleur": couleur}
        for cle, _, couleur in ETATS if valeurs[cle]
    ]


def _jour(iso):
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def _courbe(evolution):
    """Points de la courbe d'évolution (SVG), de 0 à 100 %."""
    if len(evolution) < 2:
        return None
    pas = LARGEUR_COURBE / (len(evolution) - 1)
    points = [
        (round(i * pas, 1), round(HAUTEUR_COURBE - p["pct"] * HAUTEUR_COURBE / 100, 1))
        for i, p in enumerate(evolution)
    ]
    return {
        "ligne": " ".join(f"{x},{y}" for x, y in points),
        "aire": f"0,{HAUTEUR_COURBE} " + " ".join(f"{x},{y}" for x, y in points) + f" {LARGEUR_COURBE},{HAUTEUR_COURBE}",
        "debut": _jour(evolution[0]["semaine"]),
        "fin": _jour(evolution[-1]["semaine"]),
        "dernier": evolution[-1]["pct"],
        "largeur": LARGEUR_COURBE,
        "hauteur": HAUTEUR_COURBE,
    }


def _nom(utilisateur):
    return f"{utilisateur.prenom} {utilisateur.nom}" if utilisateur else ""


def html_du_rapport(rapport):
    d = rapport.donnees
    etats = d.get("etats") or {}
    return render_to_string("rapports/rapport.html", {
        "rapport": rapport,
        "d": d,
        "titre": rapport.get_type_display(),
        "brouillon": rapport.statut != rapport.Statut.VALIDE,
        "texte": en_html(rapport.contenu),
        "courbe": _courbe(d.get("evolution", [])),
        "anneau_etats": _anneau([(libelle, etats.get(cle, 0), couleur) for cle, libelle, couleur in ETATS]),
        "anneau_genre": _anneau([(g["libelle"], g["nb"], GENRES[g["code"]]) for g in d.get("genre", [])]),
        "genre_renseigne": any(g["code"] for g in d.get("genre", [])),
        "histogramme": _histogramme(d.get("tranches")),
        "bilan_briefs": [{**l, "segments": _segments_bilan(l)} for l in d.get("bilan_briefs", [])],
        "legende_etats": ETATS,
        "definitions": DEFINITIONS,
        "auteur": _nom(rapport.auteur),
        "valide_par": _nom(rapport.valide_par),
        "date_validation": (
            timezone.localtime(rapport.date_validation).strftime("%d/%m/%Y") if rapport.date_validation else ""
        ),
    })


def pdf_du_rapport(rapport):
    from weasyprint import HTML

    return HTML(string=html_du_rapport(rapport)).write_pdf()


def nom_du_fichier(rapport):
    from django.utils.text import slugify

    d = rapport.donnees
    cible = d.get("apprenant", {}).get("nom") or d.get("promotion", "")
    prefixe = "bilan" if rapport.type == rapport.Type.APPRENANT else "rapport-promotion"
    return f"{prefixe}-{slugify(cible) or rapport.id}.pdf"
