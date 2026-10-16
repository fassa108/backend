"""
Progression d'un apprenant dans sa promotion.

- Compétences : couples compétence-niveau validés (définitifs) sur le total
  des couples des modules de la formation (modules et compétences actifs).
- Briefs : briefs assignés (publiés ou archivés) qui visent au moins une
  compétence ; un brief est validé quand la dernière évaluation du rendu
  juge acquises toutes les compétences visées.
"""

from datetime import datetime, time, timedelta

from django.db.models import Prefetch, Q
from django.utils import timezone

from pedagogie.models import CompetenceNiveau, InscriptionPromotion

from .models import Assignation, Brief, CompetenceValidee, Evaluation


class StatutBrief:
    VALIDE = "VALIDE"          # toutes les compétences visées acquises
    NON_VALIDE = "NON_VALIDE"  # évalué, au moins une compétence non acquise
    EVALUE = "EVALUE"          # évalué, brief sans compétence visée
    A_EVALUER = "A_EVALUER"    # rendu, pas encore évalué (ou redéposé depuis l'évaluation)
    NON_RENDU = "NON_RENDU"    # ni dépôt ni évaluation


def inscription_active(apprenant, tenant_id):
    return (
        InscriptionPromotion.objects.filter(apprenant=apprenant, tenant_id=tenant_id, actif=True)
        .select_related("promotion")
        .first()
    )


def _referentiel(promotion):
    return CompetenceNiveau.objects.filter(
        competence__module__formation_id=promotion.formation_id,
        competence__actif=True,
        competence__module__actif=True,
    )


def _assignations(apprenant, promotion):
    return (
        Assignation.objects.filter(
            Q(apprenant=apprenant)
            | Q(groupe__membres__apprenant=apprenant, groupe__membres__actif=True),
            brief__promotion=promotion,
            brief__statut__in=[Brief.Statut.PUBLIE, Brief.Statut.ARCHIVE],
        )
        .distinct()
        .select_related("brief")
        .prefetch_related(
            "brief__competence_niveaux",
            Prefetch("evaluations", queryset=Evaluation.objects.prefetch_related("competences")),
        )
    )


def _etat_brief(assignation):
    brief = assignation.brief
    visees = {cn.id for cn in brief.competence_niveaux.all()}
    derniere = next(iter(assignation.evaluations.all()), None)  # triées, la plus récente d'abord
    if derniere is None:
        statut = StatutBrief.A_EVALUER if assignation.livrables.exists() else StatutBrief.NON_RENDU
        acquises = set()
    else:
        acquises = {l.competence_niveau_id for l in derniere.competences.all() if l.acquis}
        if assignation.livrables.filter(date_depot__gt=derniere.date_creation).exists():
            # Nouveau dépôt depuis l'évaluation : à réévaluer
            statut = StatutBrief.A_EVALUER
        elif not visees:
            statut = StatutBrief.EVALUE
        else:
            statut = StatutBrief.VALIDE if visees <= acquises else StatutBrief.NON_VALIDE
    return {
        "id": brief.id,
        "titre": brief.titre,
        "date_limite": brief.date_limite,
        "statut": statut,
        "nb_visees": len(visees),
        "nb_acquises": len(visees & acquises),
        "date_evaluation": derniere.date_creation if derniere else None,
    }


def resume(apprenant, promotion, etats=None):
    referentiel = _referentiel(promotion)
    if etats is None:
        etats = [_etat_brief(a) for a in _assignations(apprenant, promotion)]
    comptes = [e for e in etats if e["nb_visees"]]
    return {
        "competences_validees": CompetenceValidee.objects.filter(
            apprenant=apprenant, competence_niveau__in=referentiel
        ).count(),
        "competences_total": referentiel.count(),
        "briefs_valides": sum(1 for e in comptes if e["statut"] == StatutBrief.VALIDE),
        "briefs_total": len(comptes),
    }


def detail(apprenant, promotion):
    """Résumé, référentiel module par module (validé ou non), et briefs."""
    etats = [_etat_brief(a) for a in _assignations(apprenant, promotion)]
    etats.sort(key=lambda e: e["date_limite"], reverse=True)

    validations = dict(
        CompetenceValidee.objects.filter(apprenant=apprenant).values_list(
            "competence_niveau_id", "date_validation"
        )
    )
    modules = {}
    referentiel = _referentiel(promotion).select_related(
        "competence__module", "niveau"
    ).order_by("competence__module__ordre", "competence__ordre", "niveau__ordre")
    for cn in referentiel:
        m = cn.competence.module
        module = modules.setdefault(m.id, {"id": m.id, "nom": m.nom, "competences": {}})
        c = module["competences"].setdefault(
            cn.competence_id, {"id": cn.competence_id, "nom": cn.competence.nom, "niveaux": []}
        )
        c["niveaux"].append({
            "competence_niveau": cn.id,
            "niveau": cn.niveau.nom,
            "description": cn.description,
            "valide": cn.id in validations,
            "date_validation": validations.get(cn.id),
        })

    return {
        "resume": resume(apprenant, promotion, etats),
        "modules": [
            {**m, "competences": list(m["competences"].values())} for m in modules.values()
        ],
        "briefs": etats,
        "evolution": evolution(apprenant, promotion),
    }


# ─── Évolution dans le temps (courbes) ────────────────────────────────────────

NB_SEMAINES_MAX = 52


def _fins_de_semaine(promotion, maintenant):
    """
    (lundi, fin) de chaque semaine depuis le début de la promotion jusqu'à
    aujourd'hui (les NB_SEMAINES_MAX dernières) ; la semaine en cours finit
    maintenant. Vide si la promotion n'a pas commencé.
    """
    aujourd_hui = timezone.localdate(maintenant)
    lundi = promotion.date_debut - timedelta(days=promotion.date_debut.weekday())
    semaines = []
    while lundi <= aujourd_hui:
        fin = timezone.make_aware(datetime.combine(lundi + timedelta(days=7), time.min))
        semaines.append((lundi, min(fin, maintenant)))
        lundi += timedelta(days=7)
    return semaines[-NB_SEMAINES_MAX:]


def evolution_apprenants(promotion, apprenant_ids, maintenant=None):
    """
    Semaine par semaine, compétences du référentiel validées (cumul) par
    apprenant : (semaines, {apprenant_id: [nb, …]}, total du référentiel).
    """
    maintenant = maintenant or timezone.now()
    semaines = _fins_de_semaine(promotion, maintenant)
    referentiel = _referentiel(promotion)
    dates = {}
    for apprenant_id, date in CompetenceValidee.objects.filter(
        apprenant_id__in=apprenant_ids, competence_niveau__in=referentiel
    ).values_list("apprenant_id", "date_validation"):
        dates.setdefault(apprenant_id, []).append(date)
    cumuls = {
        a: [sum(1 for d in dates.get(a, []) if d <= fin) for _, fin in semaines]
        for a in apprenant_ids
    }
    return semaines, cumuls, referentiel.count()


def _pct(valeur, total):
    return round(100 * valeur / total) if total else 0


def evolution(apprenant, promotion, maintenant=None):
    """Courbe d'un apprenant : [{semaine, validees, pct}]."""
    semaines, cumuls, total = evolution_apprenants(promotion, [apprenant.id], maintenant)
    return [
        {"semaine": lundi, "validees": n, "pct": _pct(n, total)}
        for (lundi, _), n in zip(semaines, cumuls[apprenant.id])
    ]


def evolution_promotion(promotion, apprenant_ids, maintenant=None):
    """Courbe moyenne d'une promotion : [{semaine, pct}] (moyenne des apprenants)."""
    semaines, cumuls, total = evolution_apprenants(promotion, list(apprenant_ids), maintenant)
    return [
        {
            "semaine": lundi,
            "pct": round(sum(_pct(c[i], total) for c in cumuls.values()) / len(cumuls)) if cumuls else 0,
        }
        for i, (lundi, _) in enumerate(semaines)
    ]
