"""
Qui prévenir, et quand (les emails eux-mêmes sont dans tasks.py).

- Assignation : les apprenants visés (membres actifs pour un groupe),
  tout de suite si le brief est publié, sinon à sa publication.
- Dépôt : les formateurs de la promotion, à chaque dépôt.
- Évaluation : les apprenants visés (membres actifs pour un groupe).
- Commentaire d'un pair : les apprenants visés par le rendu commenté, et
  pour une réponse l'auteur du commentaire d'origine (jamais l'auteur).
- Compétence validée : l'apprenant, dans l'application seulement.

Chaque événement crée aussi la notification de l'application (cloche),
dans la même transaction. Les emails partent une fois la transaction
validée (on_commit) : pas d'email pour une opération annulée.
"""

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from notifications.models import Notification
from notifications.services import notifier
from pedagogie.models import FormateurPromotion

from .models import Brief
from .tasks import (
    envoyer_email_assignation,
    envoyer_email_commentaire,
    envoyer_email_evaluation,
    envoyer_email_soumission,
)


def _date(dt):
    return timezone.localtime(dt).strftime("%d/%m/%Y à %H:%M")


def _tenant_id(brief):
    return brief.promotion.formation.tenant_id


def _cibles(assignation):
    """L'apprenant visé, ou les membres actifs du groupe visé."""
    if assignation.apprenant_id:
        return [assignation.apprenant]
    return [
        m.apprenant
        for m in assignation.groupe.membres.filter(actif=True).select_related("apprenant")
    ]


def _emails(utilisateurs):
    return sorted({u.email for u in utilisateurs})


def notifier_assignations(assignations):
    """Nouvelles assignations : prévenir les apprenants si le brief est publié."""
    par_brief = {}
    for a in assignations:
        if a.brief.statut == Brief.Statut.PUBLIE:
            par_brief.setdefault(a.brief, set()).update(_cibles(a))
    for brief, apprenants in par_brief.items():
        _programmer_assignation(brief, apprenants)


def notifier_publication(brief):
    """Brief publié (depuis un brouillon) : prévenir tous les apprenants assignés."""
    apprenants = set()
    for a in brief.assignations.select_related("apprenant", "groupe"):
        apprenants.update(_cibles(a))
    _programmer_assignation(brief, apprenants)


def _programmer_assignation(brief, apprenants):
    if not apprenants:
        return
    lien = f"/activites/{brief.id}"
    notifier(
        apprenants,
        Notification.Type.ASSIGNATION,
        f"Nouveau brief : {brief.titre}",
        f"À rendre avant le {_date(brief.date_limite)}.",
        lien,
        tenant=_tenant_id(brief),
    )
    args = (
        _emails(apprenants),
        brief.titre,
        _date(brief.date_limite),
        f"{settings.FRONTEND_URL}{lien}",
    )
    transaction.on_commit(lambda: envoyer_email_assignation.delay(*args))


def notifier_depot(livrable):
    """Nouveau dépôt : prévenir les formateurs de la promotion."""
    brief = livrable.assignation.brief
    formateurs = [
        fp.formateur
        for fp in FormateurPromotion.objects.filter(promotion_id=brief.promotion_id).select_related("formateur")
    ]
    if not formateurs:
        return
    a = livrable.assignation
    cible = f"le groupe {a.groupe.nom}" if a.groupe_id else f"{a.apprenant.prenom} {a.apprenant.nom}"
    deposant = f"{livrable.deposant.prenom} {livrable.deposant.nom}"
    lien = f"/briefs/{brief.id}"
    message = f"{deposant} a déposé" + (f" pour {cible}" if cible != deposant else "")
    message += f" (dépôt n°{livrable.numero})" + (", en retard." if livrable.en_retard else ".")
    notifier(
        formateurs,
        Notification.Type.DEPOT,
        f"Nouveau dépôt : {brief.titre}",
        message,
        lien,
        tenant=_tenant_id(brief),
    )
    args = (
        [f.email for f in formateurs],
        deposant,
        cible,
        brief.titre,
        livrable.numero,
        _date(livrable.date_depot),
        livrable.en_retard,
        f"{settings.FRONTEND_URL}{lien}",
    )
    transaction.on_commit(lambda: envoyer_email_soumission.delay(*args))


def notifier_evaluation(evaluation):
    """Rendu évalué : prévenir l'apprenant, ou chaque membre actif du groupe."""
    a = evaluation.assignation
    apprenants = _cibles(a)
    if not apprenants:
        return
    lignes = list(evaluation.competences.all())
    e = evaluation.evaluateur
    evaluateur = f"{e.prenom} {e.nom}" if e else "Votre formateur"
    acquises = sum(1 for l in lignes if l.acquis)
    lien = f"/activites/{a.brief_id}"
    notifier(
        apprenants,
        Notification.Type.EVALUATION,
        f"Rendu évalué : {a.brief.titre}",
        f"{evaluateur} : {acquises} compétence(s) acquise(s) sur {len(lignes)}.",
        lien,
        tenant=_tenant_id(a.brief),
    )
    args = (
        _emails(apprenants),
        a.brief.titre,
        evaluateur,
        acquises,
        len(lignes),
        f"{settings.FRONTEND_URL}{lien}",
    )
    transaction.on_commit(lambda: envoyer_email_evaluation.delay(*args))


def notifier_commentaire(commentaire):
    """Nouveau commentaire (ou réponse) : prévenir les apprenants concernés."""
    a = commentaire.assignation
    destinataires = {u.pk: u for u in _cibles(a)}
    if commentaire.parent_id:
        destinataires[commentaire.parent.auteur_id] = commentaire.parent.auteur
    destinataires.pop(commentaire.auteur_id, None)
    if not destinataires:
        return
    auteur = f"{commentaire.auteur.prenom} {commentaire.auteur.nom}"
    reponse = commentaire.parent_id is not None
    lien = f"/activites/{a.brief_id}"
    notifier(
        destinataires.values(),
        Notification.Type.COMMENTAIRE,
        "Réponse à un commentaire" if reponse else "Nouveau commentaire",
        f"{auteur} sur « {a.brief.titre} ».",
        lien,
        tenant=_tenant_id(a.brief),
    )
    args = (
        _emails(destinataires.values()),
        auteur,
        a.brief.titre,
        reponse,
        f"{settings.FRONTEND_URL}{lien}",
    )
    transaction.on_commit(lambda: envoyer_email_commentaire.delay(*args))


def notifier_competences_validees(apprenant, competence_niveaux, tenant_id):
    """Compétences acquises pour la première fois (pas d'email)."""
    if not competence_niveaux:
        return
    noms = ", ".join(f"{cn.competence.nom} ({cn.niveau.nom})" for cn in competence_niveaux)
    titre = (
        "Compétence validée" if len(competence_niveaux) == 1
        else f"{len(competence_niveaux)} compétences validées"
    )
    notifier(
        [apprenant],
        Notification.Type.COMPETENCE,
        titre,
        noms,
        "/ma-progression",
        tenant=tenant_id,
    )
