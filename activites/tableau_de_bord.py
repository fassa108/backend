"""
Tableaux de bord (formateur, admin organisme, apprenant) : ce qui attend
une action, et où en sont les promotions. Chacun est calculé en une seule
requête HTTP.

Un « rendu » est une assignation (un apprenant, ou un groupe). Son état :
- NON_RENDU : aucun dépôt ;
- A_EVALUER : déposé, et le dernier dépôt est plus récent que la dernière
  évaluation (un nouveau dépôt après évaluation est donc à réévaluer) ;
- VALIDE / NON_VALIDE : évalué, compétences visées toutes acquises ou non
  (un brief sans compétence visée compte comme validé une fois évalué).
"""

from datetime import timedelta

from django.db.models import Count, Prefetch, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from accounts.models import MembreTenant
from pedagogie.models import (
    CompetenceNiveau,
    FormateurPromotion,
    Formation,
    InscriptionPromotion,
    Promotion,
)

from . import progression
from .models import Assignation, Brief, CommentairePair, CompetenceValidee, Evaluation, Livrable

NON_RENDU = "NON_RENDU"
A_EVALUER = "A_EVALUER"
VALIDE = "VALIDE"
NON_VALIDE = "NON_VALIDE"
ETATS_RENDUS = (A_EVALUER, VALIDE, NON_VALIDE)

NB_ELEMENTS_LISTE = 6
NB_APPRENANTS_A_SUIVRE = 5
NB_RECENTS = 3
ECHEANCES_PASSEES_JOURS = 3
ECHEANCES_A_VENIR_JOURS = 14
JOURS_ACTIVITE = 14
ATTENTE_LONGUE_JOURS = 7
LONGUEUR_EXTRAIT = 140


# ─── Outils communs ───────────────────────────────────────────────────────────

def _pourcentage(valeur, total):
    return round(100 * valeur / total) if total else 0


def _moyenne(valeurs):
    return round(sum(valeurs) / len(valeurs)) if valeurs else 0


def _nom(utilisateur):
    return f"{utilisateur.prenom} {utilisateur.nom}"


def _cible(assignation):
    if assignation.groupe_id:
        return {"type": "groupe", "nom": assignation.groupe.nom}
    return {"type": "apprenant", "nom": _nom(assignation.apprenant)}


def _assignations_suivies(filtre):
    """Assignations des briefs publiés ou archivés, avec tout le nécessaire préchargé."""
    return list(
        Assignation.objects.filter(
            filtre, brief__statut__in=[Brief.Statut.PUBLIE, Brief.Statut.ARCHIVE]
        )
        .distinct()
        .select_related("brief", "groupe", "apprenant")
        .prefetch_related(
            "brief__competence_niveaux",
            "groupe__membres",
            Prefetch("livrables", queryset=Livrable.objects.order_by("-date_depot")),
            Prefetch("evaluations", queryset=Evaluation.objects.prefetch_related("competences")),
        )
    )


def _etat(assignation):
    """État d'un rendu (voir le docstring du module) et son dernier dépôt."""
    livrables = assignation.livrables.all()  # préchargés, le plus récent d'abord
    dernier_depot = livrables[0] if livrables else None
    if dernier_depot is None:
        return NON_RENDU, None

    evaluations = assignation.evaluations.all()  # préchargées, la plus récente d'abord
    derniere = evaluations[0] if evaluations else None
    if derniere is None or dernier_depot.date_depot > derniere.date_creation:
        return A_EVALUER, dernier_depot

    visees = {cn.id for cn in assignation.brief.competence_niveaux.all()}
    acquises = {c.competence_niveau_id for c in derniere.competences.all() if c.acquis}
    return (VALIDE if visees <= acquises else NON_VALIDE), dernier_depot


def _rendu_a_evaluer(assignation, dernier_depot):
    brief = assignation.brief
    return {
        "assignation": assignation.id,
        "brief": brief.id,
        "brief_titre": brief.titre,
        "cible": _cible(assignation),
        "numero": dernier_depot.numero,
        "date_depot": dernier_depot.date_depot,
        "en_retard": dernier_depot.date_depot > brief.date_limite,
    }


class _AnalysePromotions:
    """
    États des rendus, apprenants inscrits et compétences validées d'un
    ensemble de promotions, calculés une fois pour tout le tableau de bord.
    """

    def __init__(self, promotions, maintenant):
        self.promotions = promotions
        self.maintenant = maintenant
        ids = [p.id for p in promotions]

        self.rendus = [
            (a, *_etat(a)) for a in _assignations_suivies(Q(brief__promotion_id__in=ids))
        ]

        # Apprenants suivis : inscription active, ou fermée par la clôture
        self.inscrits = {}  # promotion_id → {apprenant_id: apprenant}
        for i in InscriptionPromotion.objects.filter(
            Q(actif=True) | Q(fermee_par_cloture=True), promotion_id__in=ids
        ).select_related("apprenant"):
            self.inscrits.setdefault(i.promotion_id, {})[i.apprenant_id] = i.apprenant

        self.referentiels = {}  # formation_id → nb de compétences-niveaux
        self.validees = {}  # (formation_id, apprenant_id) → nb validées
        for formation_id in {p.formation_id for p in promotions}:
            competences = Q(
                competence__module__formation_id=formation_id,
                competence__actif=True,
                competence__module__actif=True,
            )
            self.referentiels[formation_id] = CompetenceNiveau.objects.filter(competences).count()
            apprenants = {
                apprenant_id
                for p in promotions if p.formation_id == formation_id
                for apprenant_id in self.inscrits.get(p.id, {})
            }
            for ligne in (
                CompetenceValidee.objects.filter(
                    apprenant_id__in=apprenants,
                    competence_niveau__in=CompetenceNiveau.objects.filter(competences),
                )
                .values("apprenant_id")
                .annotate(nb=Count("id"))
            ):
                self.validees[(formation_id, ligne["apprenant_id"])] = ligne["nb"]

    def competences_pct(self, promotion, apprenant_id):
        return _pourcentage(
            self.validees.get((promotion.formation_id, apprenant_id), 0),
            self.referentiels[promotion.formation_id],
        )

    def competences_pct_promotion(self, promotion):
        return _moyenne([
            self.competences_pct(promotion, apprenant_id)
            for apprenant_id in self.inscrits.get(promotion.id, {})
        ])

    def taux_rendu(self, promotion_ids):
        """Rendus reçus sur rendus attendus, pour les briefs dont l'échéance est passée."""
        echus = [
            etat for a, etat, _ in self.rendus
            if a.brief.promotion_id in promotion_ids and a.brief.date_limite < self.maintenant
        ]
        return _pourcentage(sum(1 for e in echus if e != NON_RENDU), len(echus))

    def suivi_briefs(self, promotion_ids):
        """Répartition des rendus par état, brief par brief (graphique)."""
        suivi = {}
        for a, etat, _ in self.rendus:
            brief = a.brief
            if brief.promotion_id not in promotion_ids:
                continue
            ligne = suivi.setdefault(brief.id, {
                "brief": brief.id,
                "titre": brief.titre,
                "promotion": brief.promotion_id,
                "statut": brief.statut,
                "date_limite": brief.date_limite,
                NON_RENDU: 0, A_EVALUER: 0, VALIDE: 0, NON_VALIDE: 0,
            })
            ligne[etat] += 1
        return sorted(suivi.values(), key=lambda s: s["date_limite"])

    def retards(self, promotion_ids):
        """(promotion_id, apprenant_id) → nombre de briefs échus non rendus."""
        retards = {}
        for a, etat, _ in self.rendus:
            brief = a.brief
            if etat != NON_RENDU or brief.promotion_id not in promotion_ids:
                continue
            if brief.date_limite >= self.maintenant:
                continue
            if a.groupe_id:
                concernes = [m.apprenant_id for m in a.groupe.membres.all() if m.actif]
            else:
                concernes = [a.apprenant_id]
            for apprenant_id in concernes:
                cle = (brief.promotion_id, apprenant_id)
                retards[cle] = retards.get(cle, 0) + 1
        return retards


def _promotions_ouvertes(promotions):
    return {p.id for p in promotions if p.actif}


# ─── Formateur ────────────────────────────────────────────────────────────────

def tableau_de_bord_formateur(user, tenant_id):
    maintenant = timezone.now()
    promotions = list(
        Promotion.objects.filter(
            formateurs_affectes__formateur=user, formation__tenant_id=tenant_id
        )
        .select_related("formation")
        .distinct()
        .order_by("-actif", "-date_debut")
    )
    ouvertes = _promotions_ouvertes(promotions)
    analyse = _AnalysePromotions(promotions, maintenant)

    # Qui évalue un brief : son créateur, ou tout formateur de la promotion
    # si le créateur n'y est plus affecté (même règle que l'API d'évaluation).
    equipes = {}
    for promotion_id, formateur_id in FormateurPromotion.objects.filter(
        promotion_id__in=[p.id for p in promotions]
    ).values_list("promotion_id", "formateur_id"):
        equipes.setdefault(promotion_id, set()).add(formateur_id)

    def peut_evaluer(brief):
        return brief.cree_par_id == user.id or brief.cree_par_id not in equipes.get(brief.promotion_id, set())

    # Les plus anciens d'abord : ils attendent depuis le plus longtemps
    a_evaluer = sorted(
        (
            _rendu_a_evaluer(a, depot)
            for a, etat, depot in analyse.rendus
            if etat == A_EVALUER and a.brief.promotion_id in ouvertes and peut_evaluer(a.brief)
        ),
        key=lambda r: r["date_depot"],
    )

    noms = {p.id: p.nom for p in promotions}
    suivi = analyse.suivi_briefs(ouvertes)
    debut = maintenant - timedelta(days=ECHEANCES_PASSEES_JOURS)
    fin = maintenant + timedelta(days=ECHEANCES_A_VENIR_JOURS)
    echeances = [
        {
            "brief": s["brief"],
            "titre": s["titre"],
            "promotion": noms[s["promotion"]],
            "date_limite": s["date_limite"],
            "rendus": sum(s[e] for e in ETATS_RENDUS),
            "attendus": s[NON_RENDU] + sum(s[e] for e in ETATS_RENDUS),
        }
        for s in suivi
        if s["statut"] == Brief.Statut.PUBLIE and debut <= s["date_limite"] <= fin
    ]

    a_suivre = []
    par_id = {p.id: p for p in promotions}
    for (promotion_id, apprenant_id), nb in analyse.retards(ouvertes).items():
        apprenant = analyse.inscrits.get(promotion_id, {}).get(apprenant_id)
        if apprenant is None:  # désinscrit depuis
            continue
        promotion = par_id[promotion_id]
        a_suivre.append({
            "id": apprenant.id,
            "nom": _nom(apprenant),
            "promotion": promotion.nom,
            "briefs_non_rendus": nb,
            "competences_pct": analyse.competences_pct(promotion, apprenant.id),
        })
    a_suivre.sort(key=lambda s: (-s["briefs_non_rendus"], s["competences_pct"], s["nom"]))

    return {
        "indicateurs": {
            "a_evaluer": len(a_evaluer),
            "depots_7_jours": Livrable.objects.filter(
                assignation__brief__promotion_id__in=list(par_id),
                date_depot__gte=maintenant - timedelta(days=7),
            ).count(),
            "apprenants": sum(len(analyse.inscrits.get(i, {})) for i in ouvertes),
            "taux_rendu": analyse.taux_rendu(ouvertes),
            "competences_pct": _moyenne([
                analyse.competences_pct(p, apprenant_id)
                for p in promotions if p.actif
                for apprenant_id in analyse.inscrits.get(p.id, {})
            ]),
        },
        "a_evaluer": a_evaluer[:NB_ELEMENTS_LISTE],
        "echeances": echeances,
        "suivi_briefs": [{**s, "promotion": noms[s["promotion"]]} for s in suivi],
        "apprenants_a_suivre": a_suivre[:NB_APPRENANTS_A_SUIVRE],
        "promotions": [
            {
                "id": p.id,
                "nom": p.nom,
                "formation": p.formation.nom,
                "actif": p.actif,
                "nb_apprenants": len(analyse.inscrits.get(p.id, {})),
                "competences_pct": analyse.competences_pct_promotion(p),
            }
            for p in promotions
        ],
    }


# ─── Admin organisme ──────────────────────────────────────────────────────────

def tableau_de_bord_admin(tenant_id):
    maintenant = timezone.now()
    promotions = list(
        Promotion.objects.filter(formation__tenant_id=tenant_id, actif=True)
        .select_related("formation")
        .annotate(nb_formateurs=Count("formateurs_affectes", distinct=True))
        .order_by("-date_debut")
    )
    ouvertes = _promotions_ouvertes(promotions)
    analyse = _AnalysePromotions(promotions, maintenant)

    membres = MembreTenant.objects.filter(tenant_id=tenant_id, actif=True)
    depuis_7_jours = maintenant - timedelta(days=7)
    livrables = Livrable.objects.filter(
        assignation__brief__promotion__formation__tenant_id=tenant_id
    )
    evaluations = Evaluation.objects.filter(
        assignation__brief__promotion__formation__tenant_id=tenant_id
    )

    # Activité des derniers jours : dépôts et évaluations par jour
    premier_jour = (maintenant - timedelta(days=JOURS_ACTIVITE - 1)).date()
    def par_jour(queryset, champ):
        return dict(
            queryset.filter(**{f"{champ}__date__gte": premier_jour})
            .annotate(jour=TruncDate(champ))
            .values("jour")
            .annotate(nb=Count("id"))
            .values_list("jour", "nb")
        )
    depots_jour = par_jour(livrables, "date_depot")
    evaluations_jour = par_jour(evaluations, "date_creation")
    activite = []
    for i in range(JOURS_ACTIVITE):
        jour = premier_jour + timedelta(days=i)
        activite.append({
            "date": jour,
            "depots": depots_jour.get(jour, 0),
            "evaluations": evaluations_jour.get(jour, 0),
        })

    # Points d'attention
    invitations = (
        membres.filter(utilisateur__actif=False)
        .select_related("utilisateur")
        .order_by("date_ajout")
    )
    attente_longue = sum(
        1 for a, etat, depot in analyse.rendus
        if etat == A_EVALUER and depot.date_depot < maintenant - timedelta(days=ATTENTE_LONGUE_JOURS)
    )

    return {
        "indicateurs": {
            "formations": Formation.objects.filter(tenant_id=tenant_id).count(),
            "promotions_actives": len(promotions),
            "apprenants": membres.filter(role=MembreTenant.Role.APPRENANT).count(),
            "formateurs": membres.filter(role=MembreTenant.Role.FORMATEUR).count(),
            "depots_7_jours": livrables.filter(date_depot__gte=depuis_7_jours).count(),
            "evaluations_7_jours": evaluations.filter(date_creation__gte=depuis_7_jours).count(),
            "taux_rendu": analyse.taux_rendu(ouvertes),
            "competences_pct": _moyenne([
                analyse.competences_pct(p, apprenant_id)
                for p in promotions
                for apprenant_id in analyse.inscrits.get(p.id, {})
            ]),
        },
        "promotions": [
            {
                "id": p.id,
                "nom": p.nom,
                "formation": p.formation.nom,
                "nb_apprenants": len(analyse.inscrits.get(p.id, {})),
                "nb_formateurs": p.nb_formateurs,
                "competences_pct": analyse.competences_pct_promotion(p),
                "taux_rendu": analyse.taux_rendu({p.id}),
                "a_evaluer": sum(
                    1 for a, etat, _ in analyse.rendus
                    if etat == A_EVALUER and a.brief.promotion_id == p.id
                ),
            }
            for p in promotions
        ],
        "activite": activite,
        "attention": {
            "invitations_en_attente": invitations.count(),
            "invitations": [
                {
                    "id": m.id,
                    "nom": _nom(m.utilisateur),
                    "email": m.utilisateur.email,
                    "role": m.role,
                    "date_ajout": m.date_ajout,
                }
                for m in invitations[:NB_ELEMENTS_LISTE]
            ],
            "promotions_sans_formateur": [
                {"id": p.id, "nom": p.nom} for p in promotions if not p.nb_formateurs
            ],
            "rendus_en_attente_longue": attente_longue,
        },
    }


# ─── Apprenant ────────────────────────────────────────────────────────────────

def tableau_de_bord_apprenant(user, tenant_id):
    from revision.models import SupportRevision, Tentative

    inscription = progression.inscription_active(user, tenant_id)
    if inscription is None:
        return {"promotion": None}

    maintenant = timezone.now()
    promotion = inscription.promotion
    rendus = [
        (a, *_etat(a))
        for a in _assignations_suivies(
            (Q(apprenant=user) | Q(groupe__membres__apprenant=user, groupe__membres__actif=True))
            & Q(brief__promotion=promotion)
        )
    ]
    ids_assignations = [a.id for a, _, _ in rendus]

    # À faire : briefs publiés pas encore rendus, du plus urgent au moins urgent
    a_rendre = sorted(
        (
            {
                "brief": a.brief.id,
                "titre": a.brief.titre,
                "date_debut": a.brief.date_debut,
                "date_limite": a.brief.date_limite,
                "en_retard": a.brief.date_limite < maintenant,
            }
            for a, etat, _ in rendus
            if etat == NON_RENDU and a.brief.statut == Brief.Statut.PUBLIE
        ),
        key=lambda b: b["date_limite"],
    )

    # Quiz publiés des modules où l'apprenant a déposé (comme la page Révisions),
    # pas encore tentés
    modules = {a.brief.module_id for a, etat, _ in rendus if etat != NON_RENDU}
    quiz_a_faire = [
        {"id": s.id, "titre": s.titre, "module": s.module.nom}
        for s in SupportRevision.objects.filter(
            module_id__in=modules,
            type=SupportRevision.Type.QUIZ,
            statut=SupportRevision.Statut.PUBLIE,
        )
        .exclude(tentatives__apprenant=user)
        .select_related("module")
        .order_by("-date_publication")[:NB_ELEMENTS_LISTE]
    ]
    dernier_quiz = (
        Tentative.objects.filter(apprenant=user, support__module__formation_id=promotion.formation_id)
        .select_related("support")
        .order_by("-date")
        .first()
    )

    evaluations = (
        Evaluation.objects.filter(assignation_id__in=ids_assignations)
        .select_related("assignation__brief", "evaluateur")
        .prefetch_related("competences", "assignation__brief__competence_niveaux")
        .order_by("-date_creation")[:NB_RECENTS]
    )
    commentaires = (
        CommentairePair.objects.filter(assignation_id__in=ids_assignations, masque=False)
        .exclude(auteur=user)
        .select_related("auteur", "assignation__brief")
        .order_by("-date_creation")[:NB_RECENTS]
    )

    def resume_evaluation(e):
        visees = {cn.id for cn in e.assignation.brief.competence_niveaux.all()}
        acquises = {c.competence_niveau_id for c in e.competences.all() if c.acquis}
        return {
            "id": e.id,
            "brief": e.assignation.brief_id,
            "brief_titre": e.assignation.brief.titre,
            "date": e.date_creation,
            "evaluateur": _nom(e.evaluateur) if e.evaluateur else None,
            "nb_acquises": len(visees & acquises),
            "nb_visees": len(visees),
            "valide": visees <= acquises,
        }

    return {
        "promotion": {
            "id": promotion.id,
            "nom": promotion.nom,
            "formation": promotion.formation.nom,
        },
        "indicateurs": {
            "a_rendre": sum(1 for b in a_rendre if not b["en_retard"]),
            "en_retard": sum(1 for b in a_rendre if b["en_retard"]),
            "en_attente_evaluation": sum(1 for _, etat, _ in rendus if etat == A_EVALUER),
            "dernier_quiz": (
                {
                    "support": dernier_quiz.support_id,
                    "titre": dernier_quiz.support.titre,
                    "score": dernier_quiz.score,
                    "total": dernier_quiz.total,
                }
                if dernier_quiz else None
            ),
        },
        "progression": progression.resume(user, promotion),
        "a_rendre": a_rendre[:NB_ELEMENTS_LISTE],
        "quiz_a_faire": quiz_a_faire,
        "evaluations_recentes": [resume_evaluation(e) for e in evaluations],
        "commentaires_recents": [
            {
                "id": c.id,
                "auteur": _nom(c.auteur),
                "brief": c.assignation.brief_id,
                "brief_titre": c.assignation.brief.titre,
                "date": c.date_creation,
                "extrait": c.texte[:LONGUEUR_EXTRAIT],
            }
            for c in commentaires
        ],
    }
