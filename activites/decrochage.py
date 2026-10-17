"""
Détection du décrochage des apprenants, et situation envoyée à l'IA.

Seules les promotions ouvertes sont analysées (jamais une promotion
clôturée), et seuls les apprenants à l'inscription active. Signaux :
- RETARDS : au moins SEUIL_RETARDS briefs échus non rendus ;
- INACTIVITE : des briefs à rendre (ouverts ou échus, non rendus) et aucun
  dépôt depuis JOURS_SANS_DEPOT jours (comptés depuis le dernier dépôt, ou
  l'ouverture du plus ancien brief à rendre, ou l'inscription) ;
- ABSENCE : pas de connexion depuis JOURS_SANS_CONNEXION jours ;
- PROGRESSION : compétences acquises sous RATIO_PROGRESSION × la moyenne
  de la promotion, quand cette moyenne atteint MOYENNE_MINIMALE %.

Niveau : un signal → À surveiller ; deux ou plus → À risque.
Un apprenant inscrit depuis moins de DELAI_GRACE_JOURS jours n'est jamais signalé.
"""

import html

from django.utils import timezone
from django.utils.html import strip_tags

from pedagogie.models import InscriptionPromotion

from .models import Brief, CompetenceValidee, Evaluation
from .tableau_de_bord import NON_RENDU, _AnalysePromotions, _nom

SEUIL_RETARDS = 2
JOURS_SANS_DEPOT = 14
JOURS_SANS_CONNEXION = 10
RATIO_PROGRESSION = 0.5
MOYENNE_MINIMALE = 20
DELAI_GRACE_JOURS = 14

A_SURVEILLER = "A_SURVEILLER"
A_RISQUE = "A_RISQUE"
LIBELLES_NIVEAU = {A_SURVEILLER: "à surveiller", A_RISQUE: "à risque de décrochage"}

# Situation envoyée à l'IA
NB_BRIEFS_IA = 10
NB_COMPETENCES_IA = 30
NB_COMMENTAIRES_IA = 5
LONGUEUR_TEXTE_IA = 3000


def _jours(depuis, maintenant):
    return (maintenant - depuis).days


def _concernes(assignation):
    if assignation.groupe_id:
        return [m.apprenant_id for m in assignation.groupe.membres.all() if m.actif]
    return [assignation.apprenant_id]


def _signal(code, libelle, court, valeur):
    return {"code": code, "libelle": libelle, "court": court, "valeur": valeur}


def detecter(promotions, maintenant=None, analyse=None):
    """
    Apprenants signalés des promotions ouvertes, les plus à risque d'abord :
    [{apprenant, promotion, niveau, signaux, briefs_non_rendus, competences_pct}].
    """
    maintenant = maintenant or timezone.now()
    promotions = [p for p in promotions if p.actif]
    if not promotions:
        return []
    analyse = analyse or _AnalysePromotions(promotions, maintenant)
    ids = {p.id for p in promotions}

    inscriptions = {
        (i.promotion_id, i.apprenant_id): i
        for i in InscriptionPromotion.objects.filter(promotion_id__in=ids, actif=True).select_related("apprenant")
    }

    # Par (promotion, apprenant) : dernier dépôt, et ouverture du plus ancien brief à rendre
    dernier_depot, plus_ancien_a_rendre = {}, {}
    for a, etat, depot in analyse.rendus:
        brief = a.brief
        if brief.promotion_id not in ids:
            continue
        for apprenant_id in _concernes(a):
            cle = (brief.promotion_id, apprenant_id)
            if depot and (cle not in dernier_depot or depot.date_depot > dernier_depot[cle]):
                dernier_depot[cle] = depot.date_depot
            if etat == NON_RENDU and brief.statut == Brief.Statut.PUBLIE and brief.date_debut <= maintenant:
                if cle not in plus_ancien_a_rendre or brief.date_debut < plus_ancien_a_rendre[cle]:
                    plus_ancien_a_rendre[cle] = brief.date_debut

    retards = analyse.retards(ids)
    par_id = {p.id: p for p in promotions}
    moyennes = {p.id: analyse.competences_pct_promotion(p) for p in promotions}

    signales = []
    for (promotion_id, apprenant_id), inscription in inscriptions.items():
        if _jours(inscription.date_inscription, maintenant) < DELAI_GRACE_JOURS:
            continue
        promotion = par_id[promotion_id]
        apprenant = inscription.apprenant
        cle = (promotion_id, apprenant_id)
        signaux = []

        nb_retards = retards.get(cle, 0)
        if nb_retards >= SEUIL_RETARDS:
            signaux.append(_signal(
                "RETARDS", f"{nb_retards} briefs échus non rendus", f"{nb_retards} retards", nb_retards,
            ))

        if cle in plus_ancien_a_rendre:
            reference = max(
                d for d in (dernier_depot.get(cle), plus_ancien_a_rendre[cle], inscription.date_inscription) if d
            )
            jours = _jours(reference, maintenant)
            if jours >= JOURS_SANS_DEPOT:
                signaux.append(_signal(
                    "INACTIVITE", f"Aucun dépôt depuis {jours} jours", f"sans dépôt {jours} j", jours,
                ))

        connexion = apprenant.last_login or inscription.date_inscription
        jours = _jours(connexion, maintenant)
        if jours >= JOURS_SANS_CONNEXION:
            libelle = (
                f"Pas de connexion depuis {jours} jours" if apprenant.last_login
                else f"Jamais connecté depuis son inscription ({jours} jours)"
            )
            signaux.append(_signal("ABSENCE", libelle, f"sans connexion {jours} j", jours))

        pct = analyse.competences_pct(promotion, apprenant_id)
        moyenne = moyennes[promotion_id]
        if moyenne >= MOYENNE_MINIMALE and pct < RATIO_PROGRESSION * moyenne:
            signaux.append(_signal(
                "PROGRESSION",
                f"{pct} % des compétences acquises (moyenne de la promotion : {moyenne} %)",
                f"progression {pct} %",
                pct,
            ))

        if signaux:
            signales.append({
                "apprenant": apprenant,
                "promotion": promotion,
                "niveau": A_RISQUE if len(signaux) >= 2 else A_SURVEILLER,
                "signaux": signaux,
                "briefs_non_rendus": nb_retards,
                "competences_pct": pct,
                "moyenne_promotion_pct": moyenne,
            })

    signales.sort(key=lambda s: (s["niveau"] != A_RISQUE, -len(s["signaux"]), _nom(s["apprenant"])))
    return signales


def resume(signale):
    """Forme JSON d'un apprenant signalé (tableaux de bord, analyses)."""
    apprenant, promotion = signale["apprenant"], signale["promotion"]
    return {
        "id": apprenant.id,
        "nom": _nom(apprenant),
        "promotion": promotion.nom,
        "promotion_id": promotion.id,
        "niveau": signale["niveau"],
        "signaux": signale["signaux"],
        "briefs_non_rendus": signale["briefs_non_rendus"],
        "competences_pct": signale["competences_pct"],
    }


# ─── Situation envoyée à l'IA (anonymisée : ni nom ni email) ──────────────────

def _texte(contenu_html, longueur=LONGUEUR_TEXTE_IA):
    return " ".join(html.unescape(strip_tags(contenu_html or "")).split())[:longueur]


def _date(dt):
    return timezone.localtime(dt).strftime("%d/%m/%Y")


def situation(apprenant, promotion, maintenant=None):
    """
    Données de l'apprenant pour la génération d'un accompagnement, et le
    signalement courant (None s'il n'est pas signalé).
    """
    maintenant = maintenant or timezone.now()
    analyse = _AnalysePromotions([promotion], maintenant)
    signale = next(
        (s for s in detecter([promotion], maintenant, analyse) if s["apprenant"].id == apprenant.id),
        None,
    )

    rendus = [(a, etat) for a, etat, _ in analyse.rendus if apprenant.id in _concernes(a)]

    a_rendre = sorted(
        (
            a.brief for a, etat in rendus
            if etat == NON_RENDU and a.brief.statut == Brief.Statut.PUBLIE and a.brief.date_debut <= maintenant
        ),
        key=lambda b: b.date_limite,
    )[:NB_BRIEFS_IA]

    # Compétences visées par les briefs échus, toujours pas acquises
    validees = set(CompetenceValidee.objects.filter(apprenant=apprenant).values_list("competence_niveau_id", flat=True))
    competences, vues = [], set()
    for a, _ in sorted(rendus, key=lambda r: r[0].brief.date_limite):
        brief = a.brief
        if brief.date_limite >= maintenant:
            continue
        for cn in brief.competence_niveaux.all():
            if cn.id in validees or cn.id in vues:
                continue
            vues.add(cn.id)
            competences.append({
                "competence": cn.competence.nom,
                "niveau": cn.niveau.nom,
                "description": _texte(cn.description, 1000),
                "brief": brief.titre,
            })

    commentaires = [
        _texte(c, 1000)
        for c in Evaluation.objects.filter(assignation_id__in=[a.id for a, _ in rendus])
        .exclude(commentaire="")
        .order_by("-date_creation")
        .values_list("commentaire", flat=True)[:NB_COMMENTAIRES_IA]
    ]

    signaux = [s["libelle"] for s in signale["signaux"]] if signale else []
    demande = {
        "signaux": signaux or ["Aucun signal particulier : le formateur souhaite faire le point."],
        "briefs": [
            {
                "titre": b.titre,
                "statut": "en_retard" if b.date_limite < maintenant else "en_cours",
                "date_limite": _date(b.date_limite),
                "description": _texte(b.description, 4000),
                "modalites_evaluation": _texte(b.modalites_evaluation),
                "livrables_attendus": _texte(b.livrables_attendus),
            }
            for b in a_rendre
        ],
        "competences_non_acquises": competences[:NB_COMPETENCES_IA],
        "commentaires_formateur": [c for c in commentaires if c],
        "progression_pct": analyse.competences_pct(promotion, apprenant.id),
        "moyenne_promotion_pct": analyse.competences_pct_promotion(promotion),
    }
    return demande, signale


# ─── Alertes quotidiennes (notifications/tasks.py) ────────────────────────────

def _cle(signale, niveau, maintenant):
    annee, semaine, _ = timezone.localtime(maintenant).isocalendar()
    return f"decrochage-{signale['promotion'].id}-{signale['apprenant'].id}-{niveau}-{annee}-{semaine}"


def alerter_formateurs(maintenant=None):
    """
    Prévient les formateurs de la promotion de chaque apprenant signalé :
    une alerte par semaine au plus, plus une si l'apprenant passe à risque.
    Renvoie le nombre de notifications créées.
    """
    from notifications.models import Notification
    from notifications.services import notifier
    from pedagogie.models import FormateurPromotion, Promotion

    maintenant = maintenant or timezone.now()
    promotions = list(Promotion.objects.filter(actif=True).select_related("formation"))
    equipes = {}
    for promotion_id, formateur_id in FormateurPromotion.objects.filter(
        promotion_id__in=[p.id for p in promotions]
    ).values_list("promotion_id", "formateur_id"):
        equipes.setdefault(promotion_id, set()).add(formateur_id)

    creees = 0
    for s in detecter(promotions, maintenant):
        formateurs = equipes.get(s["promotion"].id, set())
        if s["niveau"] == A_SURVEILLER:
            # Déjà alertés « à risque » cette semaine : pas de retour en arrière
            formateurs -= set(
                Notification.objects.filter(
                    utilisateur_id__in=formateurs, cle=_cle(s, A_RISQUE, maintenant)
                ).values_list("utilisateur_id", flat=True)
            )
        nom = _nom(s["apprenant"])
        creees += len(notifier(
            formateurs,
            Notification.Type.ALERTE,
            f"{nom} : {LIBELLES_NIVEAU[s['niveau']]}",
            f"{s['promotion'].nom} : " + ", ".join(sig["libelle"].lower() for sig in s["signaux"]) + ".",
            f"/progression/{s['apprenant'].id}",
            tenant=s["promotion"].formation.tenant_id,
            cle=_cle(s, s["niveau"], maintenant),
        ))
    return creees
