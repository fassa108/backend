"""
Chiffres et tableaux d'un rapport, calculés par Django (l'IA ne calcule
rien), et la demande envoyée au service IA.

Les données gardées dans le rapport contiennent les noms et les emails
(tableaux du PDF). La demande à l'IA est pseudonymisée : les apprenants y
sont « Apprenant 1, 2… », sans nom ni email ; les noms sont remis dans le
texte au retour (texte.py).
"""

from django.utils import timezone

from activites.decrochage import LIBELLES_NIVEAU, _concernes, _texte, detecter
from activites.models import Evaluation
from activites.progression import detail, evolution_promotion
from activites.tableau_de_bord import (
    A_EVALUER,
    ETATS_RENDUS,
    NON_RENDU,
    NON_VALIDE,
    VALIDE,
    _AnalysePromotions,
    _competences_moins_acquises,
    _nom,
)
from accounts.models import Utilisateur
from pedagogie.models import FormateurPromotion

NB_COMMENTAIRES = 5
LIBELLES_ETAT = {
    NON_RENDU: "Non rendu",
    A_EVALUER: "À évaluer",
    VALIDE: "Validé",
    NON_VALIDE: "Non validé",
}
A_RENDRE = "A_RENDRE"  # pas encore rendu, échéance à venir
GENRES = [(Utilisateur.Genre.FEMME, "Femmes"), (Utilisateur.Genre.HOMME, "Hommes"), ("", "Non renseigné")]
TRANCHES = [(0, 25), (25, 50), (50, 75), (75, 101)]
# Chiffres par genre envoyés à l'IA seulement si chaque groupe compte
# au moins 3 apprenants : sinon la moyenne d'un groupe est une personne
MIN_GROUPE_IA = 3


def _date(dt):
    return timezone.localtime(dt).strftime("%d/%m/%Y") if dt else ""


def _jour(d):
    return d.strftime("%d/%m/%Y") if d else ""


def _premier_depot(assignation):
    livrables = list(assignation.livrables.all())  # préchargés, le plus récent d'abord
    return livrables[-1] if livrables else None


def _entete(promotion, maintenant):
    formation = promotion.formation
    periode = f"Du {_jour(promotion.date_debut)}"
    periode += f" au {_jour(promotion.date_fin)}" if promotion.date_fin else ""
    return {
        "organisme": formation.tenant.nom,
        "formation": formation.nom,
        "promotion": promotion.nom,
        "periode": periode,
        "cloturee": not promotion.actif,
        "formateurs": sorted(
            _nom(f.formateur)
            for f in FormateurPromotion.objects.filter(promotion=promotion).select_related("formateur")
        ),
        "date": _date(maintenant),
    }


def _bilan_rendus(rendus, maintenant):
    """Rendus des briefs échus : à temps, en retard (premier dépôt), manquants."""
    a_temps = en_retard = manquants = 0
    for a, etat, _ in rendus:
        if a.brief.date_limite >= maintenant:
            continue
        depot = _premier_depot(a)
        if depot is None:
            manquants += 1
        elif depot.date_depot > a.brief.date_limite:
            en_retard += 1
        else:
            a_temps += 1
    return a_temps, en_retard, manquants


def _indicateur(libelle, valeur):
    return {"libelle": libelle, "valeur": str(valeur)}


def _pct(valeur, total):
    return round(100 * valeur / total) if total else 0


def _etat_affiche(assignation, etat, maintenant):
    """État d'un rendu pour les graphiques : un non-rendu à venir n'est pas un manque."""
    if etat == NON_RENDU and assignation.brief.date_limite >= maintenant:
        return A_RENDRE
    return etat


def _etats_rendus(rendus, maintenant):
    """Répartition des rendus par état (graphique en anneau)."""
    compte = {}
    for a, etat, _ in rendus:
        cle = _etat_affiche(a, etat, maintenant)
        compte[cle] = compte.get(cle, 0) + 1
    return {cle: compte.get(cle, 0) for cle in (VALIDE, NON_VALIDE, A_EVALUER, NON_RENDU, A_RENDRE)}


def _delai_correction(rendus):
    """
    Délai moyen, en jours, entre un dépôt et son évaluation (chaque
    évaluation compte, réévaluations comprises). None sans évaluation.
    """
    delais = []
    for a, _, _ in rendus:
        depots = list(a.livrables.all())  # le plus récent d'abord
        for evaluation in a.evaluations.all():
            depot = next((l for l in depots if l.date_depot <= evaluation.date_creation), None)
            if depot:
                delais.append((evaluation.date_creation - depot.date_depot).total_seconds())
    return round(sum(delais) / len(delais) / 86400, 1) if delais else None


def _texte_delai(jours):
    if jours is None:
        return "—"
    if jours < 1:
        return f"{max(1, round(jours * 24))} h"
    return f"{jours:.1f} j".replace(".", ",")


def _bilan_briefs(rendus, maintenant):
    """Une ligne par brief : rendus attendus, reçus, validés, non validés, en retard."""
    bilan = {}
    for a, etat, _ in rendus:
        brief = a.brief
        ligne = bilan.setdefault(brief.id, {
            "titre": brief.titre,
            "date_limite": _date(brief.date_limite),
            "echu": brief.date_limite < maintenant,
            "attendus": 0, "rendus": 0, "valides": 0, "non_valides": 0, "a_evaluer": 0,
            "non_rendus": 0, "en_retard": 0,
            "_tri": brief.date_limite,
        })
        ligne["attendus"] += 1
        if etat in ETATS_RENDUS:
            ligne["rendus"] += 1
        ligne["valides"] += etat == VALIDE
        ligne["non_valides"] += etat == NON_VALIDE
        ligne["a_evaluer"] += etat == A_EVALUER
        ligne["non_rendus"] += etat == NON_RENDU
        depot = _premier_depot(a)
        ligne["en_retard"] += bool(depot and depot.date_depot > brief.date_limite)
    lignes = sorted(bilan.values(), key=lambda l: l["_tri"])
    for l in lignes:
        del l["_tri"]
        evalues = l["valides"] + l["non_valides"]
        l["taux_validation"] = _pct(l["valides"], evalues) if evalues else None
    return lignes


def _tranches(apprenants):
    """Nombre d'apprenants par tranche de progression (histogramme)."""
    return [
        {
            "tranche": f"{bas}–{min(haut, 100)} %",
            "nb": sum(1 for a in apprenants if bas <= a["progression_pct"] < haut),
        }
        for bas, haut in TRANCHES
    ]


def _par_genre(apprenants):
    """Effectif et résultats des femmes, des hommes et des non renseignés."""
    groupes = []
    for code, libelle in GENRES:
        membres = [a for a in apprenants if a["genre"] == code]
        if not membres:
            continue
        echus = sum(a["echus"] for a in membres)
        evalues = sum(a["valides"] + a["non_valides"] for a in membres)
        groupes.append({
            "code": code,
            "libelle": libelle,
            "nb": len(membres),
            "pct": _pct(len(membres), len(apprenants)),
            "progression_pct": round(sum(a["progression_pct"] for a in membres) / len(membres)),
            "taux_rendu": _pct(sum(a["echus_rendus"] for a in membres), echus),
            "taux_validation": _pct(sum(a["valides"] for a in membres), evalues),
        })
    return groupes


def _signalements(promotion, maintenant, analyse):
    """apprenant_id → signalement (décrochage), promotions ouvertes seulement."""
    return {s["apprenant"].id: s for s in detecter([promotion], maintenant, analyse)}


# ─── Rapport de promotion ─────────────────────────────────────────────────────

def donnees_promotion(promotion, maintenant=None):
    maintenant = maintenant or timezone.now()
    analyse = _AnalysePromotions([promotion], maintenant)
    inscrits = analyse.inscrits.get(promotion.id, {})
    signales = _signalements(promotion, maintenant, analyse)
    retards = analyse.retards({promotion.id})

    briefs = {a.brief_id for a, _, _ in analyse.rendus}
    a_temps, en_retard, manquants = _bilan_rendus(analyse.rendus, maintenant)
    etats = _etats_rendus(analyse.rendus, maintenant)
    evalues = etats[VALIDE] + etats[NON_VALIDE]

    vide = {"rendus": 0, "attendus": 0, "echus": 0, "echus_rendus": 0, "valides": 0, "non_valides": 0,
            "dernier_depot": None}
    par_apprenant = {}
    for a, etat, depot in analyse.rendus:
        echu = a.brief.date_limite < maintenant
        for apprenant_id in _concernes(a):
            ligne = par_apprenant.setdefault(apprenant_id, dict(vide))
            ligne["attendus"] += 1
            ligne["echus"] += echu
            if etat in ETATS_RENDUS:
                ligne["rendus"] += 1
                ligne["echus_rendus"] += echu
            ligne["valides"] += etat == VALIDE
            ligne["non_valides"] += etat == NON_VALIDE
            if depot and (ligne["dernier_depot"] is None or depot.date_depot > ligne["dernier_depot"]):
                ligne["dernier_depot"] = depot.date_depot

    apprenants = []
    for numero, apprenant in enumerate(sorted(inscrits.values(), key=_nom), start=1):
        suivi = par_apprenant.get(apprenant.id, vide)
        signale = signales.get(apprenant.id)
        apprenants.append({
            "reference": f"Apprenant {numero}",
            "nom": _nom(apprenant),
            "email": apprenant.email,
            "genre": apprenant.genre,
            "progression_pct": analyse.competences_pct(promotion, apprenant.id),
            "rendus": suivi["rendus"],
            "attendus": suivi["attendus"],
            "echus": suivi["echus"],
            "echus_rendus": suivi["echus_rendus"],
            "valides": suivi["valides"],
            "non_valides": suivi["non_valides"],
            "retards": retards.get((promotion.id, apprenant.id), 0),
            "dernier_depot": _date(suivi["dernier_depot"]),
            "niveau": LIBELLES_NIVEAU[signale["niveau"]] if signale else "",
            "signaux": [s["libelle"] for s in signale["signaux"]] if signale else [],
        })

    nb_signales = len(signales)
    return {
        **_entete(promotion, maintenant),
        "indicateurs": [
            _indicateur("Apprenants", len(inscrits)),
            _indicateur("Progression moyenne", f"{analyse.competences_pct_promotion(promotion)} %"),
            _indicateur("Briefs publiés", len(briefs)),
            _indicateur("Taux de rendu (briefs échus)", f"{analyse.taux_rendu({promotion.id})} %"),
            _indicateur("Rendus à temps", a_temps),
            _indicateur("Rendus en retard", en_retard),
            _indicateur("Rendus manquants", manquants),
            _indicateur("Rendus validés", etats[VALIDE]),
            _indicateur("Rendus non validés", etats[NON_VALIDE]),
            _indicateur("Taux de validation", f"{_pct(etats[VALIDE], evalues)} %" if evalues else "—"),
            _indicateur("Rendus à évaluer", etats[A_EVALUER]),
            _indicateur("Délai moyen de correction", _texte_delai(_delai_correction(analyse.rendus))),
            _indicateur("Apprenants à suivre", nb_signales),
        ],
        "etats": etats,
        "genre": _par_genre(apprenants),
        "tranches": _tranches(apprenants),
        "bilan_briefs": _bilan_briefs(analyse.rendus, maintenant),
        "evolution": [
            {"semaine": p["semaine"].isoformat(), "pct": p["pct"]}
            for p in evolution_promotion(promotion, inscrits, maintenant)
        ],
        "competences": [
            {k: c[k] for k in ("competence", "niveau", "module", "apprenants", "pct")}
            for c in _competences_moins_acquises(analyse, maintenant, nb=None)
        ],
        "apprenants": apprenants,
    }


# ─── Bilan individuel ─────────────────────────────────────────────────────────

def donnees_apprenant(apprenant, promotion, maintenant=None):
    maintenant = maintenant or timezone.now()
    analyse = _AnalysePromotions([promotion], maintenant)
    rendus = [(a, etat, depot) for a, etat, depot in analyse.rendus if apprenant.id in _concernes(a)]
    signale = _signalements(promotion, maintenant, analyse).get(apprenant.id)
    progression = detail(apprenant, promotion)

    a_temps, en_retard, manquants = _bilan_rendus(rendus, maintenant)
    dernier = max((depot.date_depot for _, _, depot in rendus if depot), default=None)

    briefs = []
    for a, etat, _ in sorted(rendus, key=lambda r: r[0].brief.date_limite):
        depot = _premier_depot(a)
        echu = a.brief.date_limite < maintenant
        briefs.append({
            "titre": a.brief.titre,
            "date_limite": _date(a.brief.date_limite),
            "date_rendu": _date(depot.date_depot) if depot else "",
            "en_retard": bool(depot and depot.date_depot > a.brief.date_limite) or (depot is None and echu),
            "statut": LIBELLES_ETAT[etat] if (etat != NON_RENDU or echu) else "À rendre",
        })

    commentaires = [
        c for c in (
            _texte(t, 1000)
            for t in Evaluation.objects.filter(assignation_id__in=[a.id for a, _, _ in rendus])
            .exclude(commentaire="")
            .order_by("-date_creation")
            .values_list("commentaire", flat=True)[:NB_COMMENTAIRES]
        ) if c
    ]

    pct = analyse.competences_pct(promotion, apprenant.id)
    resume = progression["resume"]
    etats = _etats_rendus(rendus, maintenant)
    par_module = []
    for m in progression["modules"]:
        niveaux = [n for c in m["competences"] for n in c["niveaux"]]
        acquises = sum(1 for n in niveaux if n["valide"])
        par_module.append({"nom": m["nom"], "acquises": acquises, "total": len(niveaux),
                           "pct": _pct(acquises, len(niveaux))})
    return {
        **_entete(promotion, maintenant),
        "apprenant": {"nom": _nom(apprenant), "email": apprenant.email},
        "indicateurs": [
            _indicateur("Progression", f"{pct} %"),
            _indicateur("Moyenne de la promotion", f"{analyse.competences_pct_promotion(promotion)} %"),
            _indicateur("Compétences acquises", f"{resume['competences_validees']} / {resume['competences_total']}"),
            _indicateur("Briefs validés", f"{resume['briefs_valides']} / {resume['briefs_total']}"),
            _indicateur("Briefs non validés", etats[NON_VALIDE]),
            _indicateur("Rendus à temps", a_temps),
            _indicateur("Rendus en retard", en_retard),
            _indicateur("Rendus manquants", manquants),
            _indicateur("Dernier dépôt", _date(dernier) or "Aucun"),
        ],
        "etats": etats,
        "par_module": par_module,
        "niveau": LIBELLES_NIVEAU[signale["niveau"]] if signale else "",
        "signaux": [s["libelle"] for s in signale["signaux"]] if signale else [],
        "evolution": [{"semaine": p["semaine"].isoformat(), "pct": p["pct"]} for p in progression["evolution"]],
        "modules": [
            {
                "nom": m["nom"],
                "competences": [
                    {
                        "competence": c["nom"],
                        "niveau": n["niveau"],
                        "valide": n["valide"],
                        "date_validation": _date(n["date_validation"]),
                    }
                    for c in m["competences"]
                    for n in c["niveaux"]
                ],
            }
            for m in progression["modules"]
        ],
        "briefs": briefs,
        "commentaires": commentaires,
    }


# ─── Demande au service IA (pseudonymisée) ────────────────────────────────────

def _indicateurs_genre(groupes):
    """Résultats des femmes et des hommes, en indicateurs pour l'IA (voir MIN_GROUPE_IA)."""
    comparables = [g for g in groupes if g["code"]]
    if len(comparables) < 2 or any(g["nb"] < MIN_GROUPE_IA for g in comparables):
        return []
    return [
        _indicateur(f"{g['libelle']} : effectif, progression, taux de rendu",
                    f"{g['nb']} ({g['pct']} %), {g['progression_pct']} %, {g['taux_rendu']} %")
        for g in comparables
    ]


def demande_ia(rapport):
    d = rapport.donnees
    demande = {
        "type": "promotion" if rapport.type == rapport.Type.PROMOTION else "apprenant",
        "formation": d["formation"],
        "promotion": d["promotion"],
        "periode": d["periode"],
        "cloturee": d["cloturee"],
        "indicateurs": d["indicateurs"] + _indicateurs_genre(d.get("genre", [])),
        "evolution": d["evolution"][-26:],
        "competences": [],
        "apprenants": [],
        "briefs": [],
        "signaux": d.get("signaux", []),
        "commentaires_formateur": d.get("commentaires", []),
    }
    if rapport.type == rapport.Type.PROMOTION:
        demande["competences"] = [
            {"competence": c["competence"], "niveau": c["niveau"], "module": c["module"], "pct": c["pct"]}
            for c in d["competences"]
        ][:150]
        demande["apprenants"] = [
            {k: a[k] for k in ("reference", "progression_pct", "rendus", "attendus", "retards", "niveau", "signaux")}
            for a in d["apprenants"]
        ][:100]
    else:
        demande["competences"] = [
            {"competence": c["competence"], "niveau": c["niveau"], "module": m["nom"], "acquise": c["valide"]}
            for m in d["modules"]
            for c in m["competences"]
        ][:150]
        demande["briefs"] = [
            {k: b[k] for k in ("titre", "date_limite", "statut", "en_retard")} for b in d["briefs"]
        ][-60:]
    return demande
