"""
Abonnements mensuels des organismes (models.Abonnement).

- À la création d'un organisme : un mois (payé à l'inscription, ou offert
  si l'admin SaaS le crée lui-même).
- Chaque paiement de renouvellement prolonge d'un mois.
- Chaque jour (verifier_abonnements) : rappels aux administrateurs de
  l'organisme 7 jours avant l'échéance, la veille et le jour même ; à la
  fin de la grâce, l'organisme est suspendu jusqu'au paiement.
"""

import calendar

from django.conf import settings
from django.db import transaction
from django.utils import timezone

RAPPELS_JOURS = (7, 1, 0)  # jours avant l'échéance

def ajouter_un_mois(jour):
    """Même jour le mois suivant (31 janvier → 28 ou 29 février)."""
    annee, mois = (jour.year + 1, 1) if jour.month == 12 else (jour.year, jour.month + 1)
    return jour.replace(year=annee, month=mois, day=min(jour.day, calendar.monthrange(annee, mois)[1]))

def creer_abonnement(tenant):
    from .models import Abonnement

    aujourd_hui = timezone.localdate()
    return Abonnement.objects.create(
        tenant=tenant,
        montant_mensuel=settings.PRIX_ABONNEMENT_FCFA,
        date_debut=aujourd_hui,
        date_fin=ajouter_un_mois(aujourd_hui),
    )

def prolonger(abonnement):
    """
    Un mois de plus, à partir de l'échéance si elle n'est pas passée
    (payer en avance ne fait pas perdre de jours). Lève une suspension
    pour impayé. Renvoie la période couverte (début, fin).
    """
    debut = max(abonnement.date_fin, timezone.localdate())
    abonnement.date_fin = ajouter_un_mois(debut)
    abonnement.suspension_levee_pour = None
    if abonnement.suspendu_pour_impaye:
        abonnement.suspendu_pour_impaye = False
        abonnement.tenant.statut = True
        abonnement.tenant.save(update_fields=["statut"])
    abonnement.save()
    return debut, abonnement.date_fin

def resume(abonnement, jour=None):
    """État de l'abonnement pour l'API (page « Mon abonnement », bannière, admin SaaS)."""
    jour = jour or timezone.localdate()
    return {
        "statut": abonnement.statut_au(jour),
        "statut_libelle": abonnement.Statut(abonnement.statut_au(jour)).label,
        "montant_mensuel": abonnement.montant_mensuel,
        "date_debut": abonnement.date_debut,
        "date_fin": abonnement.date_fin,
        "fin_grace": abonnement.fin_grace,
        "jours_restants": (abonnement.date_fin - jour).days,
        "suspendu_pour_impaye": abonnement.suspendu_pour_impaye,
    }

def _administrateurs(tenant):
    from accounts.models import MembreTenant, Utilisateur

    return list(
        Utilisateur.objects.filter(
            membres_tenants__tenant=tenant,
            membres_tenants__role=MembreTenant.Role.ADMINISTRATEUR,
            membres_tenants__actif=True,
            actif=True,
        )
    )

def _message_rappel(abonnement, jours):
    echeance = f"{abonnement.date_fin:%d/%m/%Y}"
    if jours == 0:
        return (
            "Votre abonnement expire aujourd'hui",
            f"Renouvelez-le pour garder l'accès à EduHub : l'organisme sera suspendu après le "
            f"{abonnement.fin_grace:%d/%m/%Y}.",
        )
    quand = "demain" if jours == 1 else f"dans {jours} jours"
    return f"Votre abonnement expire {quand}", f"Échéance le {echeance} : pensez à le renouveler."

def verifier_abonnements(jour=None):
    """
    Tâche quotidienne : rappels d'échéance, puis suspension des organismes
    dont la grâce est passée. Renvoie {rappels, suspendus}.
    """
    from notifications.models import Notification
    from notifications.services import notifier

    from .models import Abonnement
    from .tasks import envoyer_email_abonnement

    jour = jour or timezone.localdate()
    rappels = suspendus = 0
    for abonnement in Abonnement.objects.select_related("tenant").filter(tenant__statut=True):
        tenant = abonnement.tenant
        jours = (abonnement.date_fin - jour).days

        if jours in RAPPELS_JOURS:
            titre, message = _message_rappel(abonnement, jours)
            prevenus = notifier(
                _administrateurs(tenant), Notification.Type.ABONNEMENT, titre, message, "/abonnement",
                tenant=tenant, cle=f"abonnement-{tenant.id}-{abonnement.date_fin}-J{jours}",
            )
            if prevenus:
                rappels += 1
                emails = [u.email for u in _administrateurs(tenant) if u.id in prevenus]
                transaction.on_commit(lambda e=emails, t=titre, m=message, n=tenant.nom: (
                    envoyer_email_abonnement.delay(e, n, t, m)
                ))

        if abonnement.statut_au(jour) == Abonnement.Statut.EXPIRE and abonnement.suspension_levee_pour != abonnement.date_fin:
            suspendre_pour_impaye(abonnement)
            suspendus += 1
    return {"rappels": rappels, "suspendus": suspendus}

@transaction.atomic
def suspendre_pour_impaye(abonnement):
    from accounts.models import Utilisateur
    from notifications.models import Notification
    from notifications.services import notifier

    from .tasks import envoyer_email_abonnement

    tenant = abonnement.tenant
    tenant.statut = False
    tenant.save(update_fields=["statut"])
    abonnement.suspendu_pour_impaye = True
    abonnement.save(update_fields=["suspendu_pour_impaye", "date_modification"])

    titre = "Organisme suspendu : abonnement expiré"
    message = "Renouvelez l'abonnement pour rétablir immédiatement l'accès de tous les membres."
    admins = _administrateurs(tenant)
    notifier(admins, Notification.Type.ABONNEMENT, titre, message, "/abonnement", tenant=tenant)
    emails = [u.email for u in admins]
    transaction.on_commit(lambda: envoyer_email_abonnement.delay(emails, tenant.nom, titre, message))
    notifier(
        Utilisateur.objects.filter(est_admin_saas=True, actif=True),
        Notification.Type.ABONNEMENT,
        f"{tenant.nom} suspendu pour impayé",
        f"Abonnement échu le {abonnement.date_fin:%d/%m/%Y}, grâce terminée.",
        f"/admin/organismes/{tenant.id}",
    )

def lever_suspension_par_admin_saas(tenant):
    """
    L'admin SaaS réactive un organisme suspendu pour impayé : pas de
    nouvelle suspension automatique avant la prochaine échéance.
    """
    abonnement = getattr(tenant, "abonnement", None)
    if abonnement and abonnement.suspendu_pour_impaye:
        abonnement.suspendu_pour_impaye = False
        abonnement.suspension_levee_pour = abonnement.date_fin
        abonnement.save(update_fields=["suspendu_pour_impaye", "suspension_levee_pour", "date_modification"])


def abonnement_de(tenant):
    """Abonnement de l'organisme, créé au besoin (organisme créé hors du service)."""
    from .models import Abonnement

    try:
        return tenant.abonnement
    except Abonnement.DoesNotExist:
        return creer_abonnement(tenant)
