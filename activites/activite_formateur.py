"""
Activité d'un formateur dans un organisme (page « Mon activité ») :
calculée à partir de ce qu'il a fait, sans journal à part.

Actions comptées : brief publié (date_publication), rendu évalué,
ressource ajoutée, quiz ou fiche de révision publié.

Indicateurs, sur les PERIODE_JOURS derniers jours comparés aux précédents :
- délai moyen de correction : du dernier dépôt avant l'évaluation à l'évaluation ;
- part des évaluations avec un commentaire écrit ;
- actions par type.
Et, à l'instant : rendus qu'il doit évaluer en attente depuis plus de
ATTENTE_JOURS jours ; semaines actives sur les NB_SEMAINES dernières.
"""

from datetime import datetime, time, timedelta

from django.db.models import Prefetch
from django.utils import timezone

from pedagogie.models import FormateurPromotion

from .models import Brief, Evaluation, Livrable, Ressource
from .tableau_de_bord import A_EVALUER, _AnalysePromotions, _nom

JOURS_GRILLE = 365
PERIODE_JOURS = 30
ATTENTE_JOURS = 3
NB_SEMAINES = 8
NB_FIL = 20

BRIEF = "BRIEF"
EVALUATION = "EVALUATION"
RESSOURCE = "RESSOURCE"
REVISION = "REVISION"
TYPES = (BRIEF, EVALUATION, RESSOURCE, REVISION)


def _actions(formateur, tenant_id, depuis):
    """Toutes les actions du formateur depuis une date : [{type, date, libelle, lien}]."""
    from revision.models import SupportRevision

    actions = []
    for b in Brief.objects.filter(
        cree_par=formateur, promotion__formation__tenant_id=tenant_id, date_publication__gte=depuis
    ):
        actions.append({"type": BRIEF, "date": b.date_publication, "libelle": f"Brief publié : {b.titre}",
                        "lien": f"/briefs/{b.id}"})

    for e in Evaluation.objects.filter(
        evaluateur=formateur, assignation__brief__promotion__formation__tenant_id=tenant_id, date_creation__gte=depuis
    ).select_related("assignation__brief", "assignation__apprenant", "assignation__groupe"):
        a = e.assignation
        cible = f"groupe {a.groupe.nom}" if a.groupe_id else _nom(a.apprenant)
        actions.append({"type": EVALUATION, "date": e.date_creation,
                        "libelle": f"Rendu évalué : {cible}, {a.brief.titre}", "lien": f"/briefs/{a.brief_id}"})

    for r in Ressource.objects.filter(formateur=formateur, tenant_id=tenant_id, date_creation__gte=depuis):
        actions.append({"type": RESSOURCE, "date": r.date_creation, "libelle": f"Ressource ajoutée : {r.titre}",
                        "lien": "/ressources"})

    for s in SupportRevision.objects.filter(
        cree_par=formateur, module__formation__tenant_id=tenant_id,
        statut=SupportRevision.Statut.PUBLIE, date_publication__gte=depuis,
    ):
        nature = "Quiz publié" if s.est_quiz else "Fiche publiée"
        actions.append({"type": REVISION, "date": s.date_publication, "libelle": f"{nature} : {s.titre}",
                        "lien": f"/revision/{s.id}"})

    actions.sort(key=lambda a: a["date"], reverse=True)
    return actions


def _delai_moyen_heures(evaluations):
    """Moyenne, en heures, entre le dernier dépôt précédant chaque évaluation et celle-ci."""
    delais = []
    for e in evaluations:
        depots = [l.date_depot for l in e.assignation.livrables.all() if l.date_depot <= e.date_creation]
        if depots:
            delais.append((e.date_creation - max(depots)).total_seconds() / 3600)
    return round(sum(delais) / len(delais), 1) if delais else None


def _periode(formateur, tenant_id, debut, fin, actions):
    evaluations = list(
        Evaluation.objects.filter(
            evaluateur=formateur,
            assignation__brief__promotion__formation__tenant_id=tenant_id,
            date_creation__gte=debut,
            date_creation__lt=fin,
        ).prefetch_related(Prefetch("assignation__livrables", queryset=Livrable.objects.only("date_depot", "assignation_id")))
    )
    avec_commentaire = sum(1 for e in evaluations if e.commentaire.strip())
    return {
        "actions": {t: sum(1 for a in actions if a["type"] == t and debut <= a["date"] < fin) for t in TYPES},
        "evaluations": len(evaluations),
        "delai_moyen_correction_h": _delai_moyen_heures(evaluations),
        "feedback_pct": round(100 * avec_commentaire / len(evaluations)) if evaluations else None,
    }


def _rendus_en_attente(formateur, tenant_id, maintenant):
    """Rendus de ses promotions ouvertes, qu'il peut évaluer, déposés il y a plus de ATTENTE_JOURS jours."""
    from pedagogie.models import Promotion

    promotions = list(
        Promotion.objects.filter(formateurs_affectes__formateur=formateur, formation__tenant_id=tenant_id, actif=True)
        .distinct()
    )
    if not promotions:
        return 0
    equipes = {}
    for promotion_id, formateur_id in FormateurPromotion.objects.filter(
        promotion_id__in=[p.id for p in promotions]
    ).values_list("promotion_id", "formateur_id"):
        equipes.setdefault(promotion_id, set()).add(formateur_id)

    limite = maintenant - timedelta(days=ATTENTE_JOURS)
    return sum(
        1
        for a, etat, depot in _AnalysePromotions(promotions, maintenant).rendus
        if etat == A_EVALUER
        and depot.date_depot < limite
        and (a.brief.cree_par_id == formateur.id or a.brief.cree_par_id not in equipes.get(a.brief.promotion_id, set()))
    )


def activite(formateur, tenant_id, maintenant=None):
    maintenant = maintenant or timezone.now()
    aujourd_hui = timezone.localdate(maintenant)
    premier_jour = aujourd_hui - timedelta(days=JOURS_GRILLE - 1)
    debut_grille = timezone.make_aware(datetime.combine(premier_jour, time.min))
    actions = _actions(formateur, tenant_id, debut_grille)

    par_jour = {}
    for a in actions:
        jour = timezone.localdate(a["date"])
        par_jour[jour] = par_jour.get(jour, 0) + 1

    # Semaines actives : semaines (lundi → dimanche) avec au moins une action
    lundi = aujourd_hui - timedelta(days=aujourd_hui.weekday())
    semaines_actives = sum(
        1
        for i in range(NB_SEMAINES)
        if any(
            par_jour.get(lundi - timedelta(weeks=i) + timedelta(days=j))
            for j in range(7)
        )
    )

    periode = timedelta(days=PERIODE_JOURS)
    return {
        "grille": [
            {"date": premier_jour + timedelta(days=i), "actions": par_jour.get(premier_jour + timedelta(days=i), 0)}
            for i in range(JOURS_GRILLE)
        ],
        "total_annee": len(actions),
        "periode_jours": PERIODE_JOURS,
        "periode": _periode(formateur, tenant_id, maintenant - periode, maintenant, actions),
        "periode_precedente": _periode(formateur, tenant_id, maintenant - 2 * periode, maintenant - periode, actions),
        "rendus_en_attente": _rendus_en_attente(formateur, tenant_id, maintenant),
        "attente_jours": ATTENTE_JOURS,
        "semaines_actives": semaines_actives,
        "nb_semaines": NB_SEMAINES,
        "fil": actions[:NB_FIL],
    }
