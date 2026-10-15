"""
Rappels d'échéance des briefs, vérifiés toutes les heures (Celery beat).

Pour chaque apprenant visé qui n'a rien déposé (lui, ou son groupe) :
- RAPPEL : quand SEUIL_RAPPEL du délai (ouverture → échéance) est écoulé,
  tant que l'échéance n'est pas passée ;
- RETARD : juste après l'échéance (dans les FENETRE_RETARD_H heures).

Ni brouillon, ni promotion clôturée, ni apprenant désinscrit (ou membre
inactif du groupe). La clé de notification garantit un seul envoi.

La notification part tout de suite ; l'email seulement entre
HEURE_DEBUT_EMAILS et HEURE_FIN_EMAILS, sinon il est décalé au matin, sauf
rappel dont l'échéance tombe avant le matin + MARGE_MATIN_H : il part
alors tout de suite. Au moment d'un envoi, la situation est revérifiée
(tasks.envoyer_email_rappel).
"""

from datetime import timedelta

from django.utils import timezone

from notifications.models import Notification
from notifications.services import notifier
from pedagogie.models import InscriptionPromotion

from .models import Assignation, Brief

SEUIL_RAPPEL = 0.7
FENETRE_RETARD_H = 24
HEURE_DEBUT_EMAILS = 7
HEURE_FIN_EMAILS = 22
MARGE_MATIN_H = 2

RAPPEL = "RAPPEL"
RETARD = "RETARD"


def _date(dt):
    return timezone.localtime(dt).strftime("%d/%m/%Y à %H:%M")


def moment_du_rappel(brief):
    return brief.date_debut + SEUIL_RAPPEL * (brief.date_limite - brief.date_debut)


def moment_email(maintenant, echeance, type_rappel):
    """
    Quand envoyer l'email : None = tout de suite ; sinon la date du matin
    où le décaler (nuit), sauf rappel urgent (échéance avant le matin + marge).
    """
    local = timezone.localtime(maintenant)
    if HEURE_DEBUT_EMAILS <= local.hour < HEURE_FIN_EMAILS:
        return None
    matin = local.replace(hour=HEURE_DEBUT_EMAILS, minute=0, second=0, microsecond=0)
    if local.hour >= HEURE_FIN_EMAILS:
        matin += timedelta(days=1)
    if type_rappel == RAPPEL and echeance < matin + timedelta(hours=MARGE_MATIN_H):
        return None
    return matin


def textes(type_rappel, brief):
    """(titre, message) de la notification."""
    if type_rappel == RAPPEL:
        return (
            f"Rappel : {brief.titre}",
            f"À rendre avant le {_date(brief.date_limite)}. Pensez à déposer votre travail.",
        )
    return (
        f"Échéance dépassée : {brief.titre}",
        "Aucun dépôt reçu. Déposez votre travail dès que possible.",
    )


def apprenants_a_relancer(assignation):
    """Apprenants vises, toujours inscrits (et membres actifs pour un groupe)."""
    if assignation.groupe_id:
        vises = [m.apprenant for m in assignation.groupe.membres.all() if m.actif]
    else:
        vises = [assignation.apprenant]
    inscrits = set(
        InscriptionPromotion.objects.filter(
            promotion_id=assignation.brief.promotion_id,
            apprenant_id__in=[a.id for a in vises],
            actif=True,
        ).values_list("apprenant_id", flat=True)
    )
    return [a for a in vises if a.id in inscrits]


def a_relancer(assignation, type_rappel, maintenant):
    """La situation justifie-t-elle (encore) ce rappel ?"""
    brief = assignation.brief
    if brief.statut != Brief.Statut.PUBLIE or not brief.promotion.actif:
        return False
    if assignation.livrables.exists():
        return False
    if type_rappel == RAPPEL:
        return moment_du_rappel(brief) <= maintenant < brief.date_limite
    return brief.date_limite <= maintenant


def envoyer_rappels(maintenant=None):
    """Crée les rappels dus et programme leurs emails. Renvoie le nombre de notifications."""
    from .tasks import envoyer_email_rappel

    maintenant = maintenant or timezone.now()
    assignations = (
        Assignation.objects.filter(
            brief__statut=Brief.Statut.PUBLIE,
            brief__promotion__actif=True,
            brief__date_debut__lte=maintenant,
            brief__date_limite__gt=maintenant - timedelta(hours=FENETRE_RETARD_H),
            livrables__isnull=True,
        )
        .select_related("brief__promotion__formation", "apprenant", "groupe")
        .prefetch_related("groupe__membres__apprenant")
    )

    creees = 0
    for assignation in assignations:
        brief = assignation.brief
        type_rappel = RETARD if brief.date_limite <= maintenant else RAPPEL
        if not a_relancer(assignation, type_rappel, maintenant):
            continue
        titre, message = textes(type_rappel, brief)
        notifies = notifier(
            apprenants_a_relancer(assignation),
            Notification.Type.RAPPEL,
            titre,
            message,
            f"/activites/{brief.id}",
            tenant=brief.promotion.formation.tenant_id,
            cle=f"rappel-brief-{brief.id}-{type_rappel.lower()}",
        )
        creees += len(notifies)
        eta = moment_email(maintenant, brief.date_limite, type_rappel)
        for apprenant_id in notifies:
            envoyer_email_rappel.apply_async((assignation.id, apprenant_id, type_rappel), eta=eta)
    return creees
