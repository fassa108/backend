"""
Qui prévenir, et quand (les emails eux-mêmes sont dans tasks.py).

- Assignation : les apprenants visés (membres actifs pour un groupe),
  tout de suite si le brief est publié, sinon à sa publication.
- Dépôt : les formateurs de la promotion, à chaque dépôt.
- Évaluation : les apprenants visés (membres actifs pour un groupe).

Les envois partent une fois la transaction validée (on_commit) : pas
d'email pour une opération annulée.
"""

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from pedagogie.models import FormateurPromotion

from .models import Brief
from .tasks import envoyer_email_assignation, envoyer_email_evaluation, envoyer_email_soumission


def _date(dt):
    return timezone.localtime(dt).strftime("%d/%m/%Y à %H:%M")


def _emails_cibles(assignation):
    if assignation.apprenant_id:
        return [assignation.apprenant.email]
    return list(
        assignation.groupe.membres.filter(actif=True).values_list("apprenant__email", flat=True)
    )


def notifier_assignations(assignations):
    """Nouvelles assignations : prévenir les apprenants si le brief est publié."""
    par_brief = {}
    for a in assignations:
        if a.brief.statut == Brief.Statut.PUBLIE:
            par_brief.setdefault(a.brief, set()).update(_emails_cibles(a))
    for brief, emails in par_brief.items():
        _programmer_assignation(brief, emails)


def notifier_publication(brief):
    """Brief publié (depuis un brouillon) : prévenir tous les apprenants assignés."""
    emails = set()
    for a in brief.assignations.select_related("apprenant", "groupe"):
        emails.update(_emails_cibles(a))
    _programmer_assignation(brief, emails)


def _programmer_assignation(brief, emails):
    if not emails:
        return
    args = (
        sorted(emails),
        brief.titre,
        _date(brief.date_limite),
        f"{settings.FRONTEND_URL}/activites/{brief.id}",
    )
    transaction.on_commit(lambda: envoyer_email_assignation.delay(*args))


def notifier_depot(livrable):
    """Nouveau dépôt : prévenir les formateurs de la promotion."""
    brief = livrable.assignation.brief
    emails = list(
        FormateurPromotion.objects.filter(promotion_id=brief.promotion_id)
        .values_list("formateur__email", flat=True)
    )
    if not emails:
        return
    a = livrable.assignation
    cible = f"le groupe {a.groupe.nom}" if a.groupe_id else f"{a.apprenant.prenom} {a.apprenant.nom}"
    args = (
        emails,
        f"{livrable.deposant.prenom} {livrable.deposant.nom}",
        cible,
        brief.titre,
        livrable.numero,
        _date(livrable.date_depot),
        livrable.en_retard,
        f"{settings.FRONTEND_URL}/briefs/{brief.id}",
    )
    transaction.on_commit(lambda: envoyer_email_soumission.delay(*args))


def notifier_evaluation(evaluation):
    """Rendu évalué : prévenir l'apprenant, ou chaque membre actif du groupe."""
    a = evaluation.assignation
    emails = _emails_cibles(a)
    if not emails:
        return
    lignes = list(evaluation.competences.all())
    e = evaluation.evaluateur
    args = (
        sorted(emails),
        a.brief.titre,
        f"{e.prenom} {e.nom}" if e else "Votre formateur",
        sum(1 for l in lignes if l.acquis),
        len(lignes),
        f"{settings.FRONTEND_URL}/activites/{a.brief_id}",
    )
    transaction.on_commit(lambda: envoyer_email_evaluation.delay(*args))
