"""
Finances de la plateforme (admin SaaS) : encaissements PayDunya
(inscriptions et renouvellements), tentatives, abonnements à échéance.

Les paiements de test (sandbox) et de production sont séparés : « mode »
choisit lesquels sont comptés (par défaut, le mode actuel de PayDunya).
"""

import io
from datetime import date

from django.db.models import Count, Q, Sum
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from .abonnements import abonnement_de
from .models import Abonnement, DemandeInscription, Paiement, Tenant

NB_MOIS = 12
NB_TRANSACTIONS = 100
ECHEANCES_JOURS = 14
MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def _debut_mois(jour, decalage=0):
    """Premier jour du mois, décalé de « decalage » mois (négatif : dans le passé)."""
    total = jour.year * 12 + jour.month - 1 + decalage
    return date(total // 12, total % 12 + 1, 1)


def _somme(qs):
    return qs.aggregate(total=Sum("montant"))["total"] or 0


def _organisme(paiement):
    if paiement.tenant_id:
        return paiement.tenant.nom
    return paiement.demande.nom_organisme if paiement.demande_id else ""


def transactions(mode, statut="", type_=""):
    qs = Paiement.objects.filter(mode=mode).select_related("tenant", "demande").order_by("-date_paiement", "-id")
    if statut:
        qs = qs.filter(statut=statut)
    if type_:
        qs = qs.filter(type=type_)
    return qs


def ligne(paiement):
    return {
        "id": paiement.id,
        "date": paiement.date_confirmation or paiement.date_paiement,
        "organisme": _organisme(paiement),
        "organisme_id": paiement.tenant_id,
        "type": paiement.type,
        "type_libelle": paiement.get_type_display(),
        "statut": paiement.statut,
        "statut_libelle": paiement.get_statut_display(),
        "montant": paiement.montant,
        "telephone": paiement.telephone,
        "reference_transaction": paiement.reference_transaction,
        "url_recu": paiement.url_recu,
        "periode_fin": paiement.periode_fin,
    }


def tableau(mode, statut="", type_=""):
    aujourd_hui = timezone.localdate()
    reussis = Paiement.objects.filter(mode=mode, statut=Paiement.Statut.REUSSI)
    debut_mois = _debut_mois(aujourd_hui)
    ce_mois = _somme(reussis.filter(date_confirmation__date__gte=debut_mois))
    mois_precedent = _somme(reussis.filter(
        date_confirmation__date__gte=_debut_mois(aujourd_hui, -1), date_confirmation__date__lt=debut_mois,
    ))
    nb_reussis = reussis.count()
    total = _somme(reussis)

    # Encaissements des 12 derniers mois, par type
    par_mois = []
    for decalage in range(-(NB_MOIS - 1), 1):
        debut, fin = _debut_mois(aujourd_hui, decalage), _debut_mois(aujourd_hui, decalage + 1)
        du_mois = reussis.filter(date_confirmation__date__gte=debut, date_confirmation__date__lt=fin)
        par_mois.append({
            "mois": f"{MOIS[debut.month - 1]} {debut:%y}",
            "inscriptions": _somme(du_mois.filter(type=Paiement.Type.INSCRIPTION)),
            "renouvellements": _somme(du_mois.filter(type=Paiement.Type.RENOUVELLEMENT)),
        })

    repartition = dict(
        Paiement.objects.filter(mode=mode).values_list("statut").annotate(nb=Count("id")).values_list("statut", "nb")
    )
    demandes = DemandeInscription.objects.aggregate(
        total=Count("id"), payees=Count("id", filter=Q(statut=DemandeInscription.Statut.PAYEE)),
    )

    # Abonnements : où en sont les organismes
    for tenant in Tenant.objects.filter(abonnement__isnull=True):
        abonnement_de(tenant)
    abonnements = list(Abonnement.objects.select_related("tenant"))
    statuts = [a.statut_au(aujourd_hui) for a in abonnements]
    echeances = sorted(
        (
            {
                "organisme": a.tenant.nom,
                "organisme_id": a.tenant_id,
                "date_fin": a.date_fin,
                "jours_restants": (a.date_fin - aujourd_hui).days,
                "statut": a.statut_au(aujourd_hui),
                "suspendu": a.suspendu_pour_impaye,
            }
            for a in abonnements
            if (a.date_fin - aujourd_hui).days <= ECHEANCES_JOURS
        ),
        key=lambda e: e["date_fin"],
    )

    return {
        "mode": mode,
        "indicateurs": {
            "ce_mois": ce_mois,
            "mois_precedent": mois_precedent,
            "evolution_pct": round(100 * (ce_mois - mois_precedent) / mois_precedent) if mois_precedent else None,
            "total": total,
            "nb_reussis": nb_reussis,
            "montant_moyen": round(total / nb_reussis) if nb_reussis else 0,
            "taux_conversion": round(100 * demandes["payees"] / demandes["total"]) if demandes["total"] else 0,
            "revenu_mensuel_attendu": sum(
                a.montant_mensuel for a, s in zip(abonnements, statuts) if s != Abonnement.Statut.EXPIRE
            ),
        },
        "par_mois": par_mois,
        "repartition": {s: repartition.get(s, 0) for s in Paiement.Statut.values},
        "abonnements": {s: statuts.count(s) for s in Abonnement.Statut.values},
        "echeances": echeances,
        "transactions": [ligne(p) for p in transactions(mode, statut, type_)[:NB_TRANSACTIONS]],
    }


ENTETE = Font(bold=True, color="FFFFFF")
FOND_ENTETE = PatternFill("solid", fgColor="4F46E5")
GRAS = Font(bold=True)
FORMAT_MONTANT = '#,##0 "FCFA"'


def _entete(feuille, titres, largeurs):
    feuille.append(titres)
    for cellule in feuille[feuille.max_row]:
        cellule.font, cellule.fill = ENTETE, FOND_ENTETE
    for i, largeur in enumerate(largeurs):
        feuille.column_dimensions[chr(ord("A") + i)].width = largeur


def export_excel(mode, statut="", type_=""):
    """
    Classeur Excel : les transactions (mêmes filtres que la page, montants
    et dates exploitables, total encaissé) et une synthèse (indicateurs,
    encaissements des 12 derniers mois).
    """
    classeur = Workbook()

    # ─── Transactions ───
    feuille = classeur.active
    feuille.title = "Transactions"
    _entete(
        feuille,
        ["Date", "Organisme", "Type", "Statut", "Montant", "Téléphone", "Référence PayDunya",
         "Abonnement jusqu'au", "Reçu"],
        [18, 30, 16, 12, 16, 18, 26, 20, 12],
    )
    total = 0
    for p in transactions(mode, statut, type_):
        l = ligne(p)
        feuille.append([
            timezone.localtime(l["date"]).replace(tzinfo=None), l["organisme"], l["type_libelle"],
            l["statut_libelle"], l["montant"], l["telephone"], l["reference_transaction"],
            l["periode_fin"], "Voir le reçu" if l["url_recu"] else "",
        ])
        n = feuille.max_row
        feuille[f"A{n}"].number_format = "dd/mm/yyyy hh:mm"
        feuille[f"E{n}"].number_format = FORMAT_MONTANT
        feuille[f"H{n}"].number_format = "dd/mm/yyyy"
        if l["url_recu"]:
            feuille[f"I{n}"].hyperlink = l["url_recu"]
            feuille[f"I{n}"].style = "Hyperlink"
        if p.statut == Paiement.Statut.REUSSI:
            total += p.montant
    feuille.freeze_panes = "A2"
    feuille.auto_filter.ref = f"A1:I{feuille.max_row}"
    feuille.append([])
    feuille.append(["Total encaissé (paiements réussis)", None, None, None, total])
    n = feuille.max_row
    feuille[f"A{n}"].font = feuille[f"E{n}"].font = GRAS
    feuille[f"E{n}"].number_format = FORMAT_MONTANT

    # ─── Synthèse ───
    donnees = tableau(mode)
    ind = donnees["indicateurs"]
    synthese = classeur.create_sheet("Synthèse")
    synthese.append([f"Finances EduHub ({'production' if mode == Paiement.Mode.LIVE else 'test'})"])
    synthese["A1"].font = Font(bold=True, size=14)
    synthese.append([f"Édité le {timezone.localtime():%d/%m/%Y à %H:%M}"])
    synthese.append([])
    _entete(synthese, ["Indicateur", "Valeur"], [34, 18])
    for libelle, valeur, format_ in [
        ("Encaissé ce mois-ci", ind["ce_mois"], FORMAT_MONTANT),
        ("Encaissé le mois précédent", ind["mois_precedent"], FORMAT_MONTANT),
        ("Total encaissé", ind["total"], FORMAT_MONTANT),
        ("Paiements réussis", ind["nb_reussis"], "0"),
        ("Montant moyen", ind["montant_moyen"], FORMAT_MONTANT),
        ("Taux de conversion des inscriptions", ind["taux_conversion"] / 100, "0 %"),
        ("Revenu mensuel attendu", ind["revenu_mensuel_attendu"], FORMAT_MONTANT),
    ]:
        synthese.append([libelle, valeur])
        synthese[f"B{synthese.max_row}"].number_format = format_
    synthese.append([])
    _entete(synthese, ["Mois", "Inscriptions", "Renouvellements", "Total"], [34, 18, 18, 18])
    for mois in donnees["par_mois"]:
        synthese.append([mois["mois"], mois["inscriptions"], mois["renouvellements"],
                         mois["inscriptions"] + mois["renouvellements"]])
        for colonne in "BCD":
            synthese[f"{colonne}{synthese.max_row}"].number_format = FORMAT_MONTANT

    sortie = io.BytesIO()
    classeur.save(sortie)
    return sortie.getvalue()
