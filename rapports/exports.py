"""
Exports Excel (openpyxl) : progression d'une promotion, suivi des
livrables et des évaluations, membres de l'organisme.
"""

from io import BytesIO

from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from accounts.models import MembreTenant, Utilisateur
from activites.models import CompetenceValidee
from activites.progression import _referentiel
from activites.tableau_de_bord import NON_RENDU, _assignations_suivies, _etat, _nom
from pedagogie.models import FormateurPromotion, InscriptionPromotion

from .donnees import LIBELLES_ETAT, _premier_depot, donnees_promotion

LARGEUR_MAX = 60
FORMAT_DATE = "DD/MM/YYYY"
FORMAT_DATE_HEURE = "DD/MM/YYYY HH:MM"
FORMAT_PCT = "0%"
LIBELLES_GENRE = dict(Utilisateur.Genre.choices)

ENTETE = Font(bold=True, color="FFFFFF")
FOND_ENTETE = PatternFill("solid", fgColor="4F46E5")


def _local(dt):
    """Date et heure locales sans fuseau (Excel ne les accepte pas)."""
    return timezone.localtime(dt).replace(tzinfo=None) if dt else None


def nom_du_fichier(prefixe, cible):
    return f"{prefixe}-{slugify(cible) or 'export'}-{timezone.localdate():%Y-%m-%d}.xlsx"


def _feuille(ws, titre, infos, colonnes, lignes):
    """
    Titre et informations en tête, puis le tableau (en-tête figé et filtrable).
    colonnes : [(intitulé, format ou None)] ; lignes : listes de valeurs.
    """
    ws.append([titre])
    ws["A1"].font = Font(bold=True, size=14)
    for info in infos:
        ws.append([info])
        ws.cell(row=ws.max_row, column=1).font = Font(color="64748B")
    ws.append([])

    debut = ws.max_row + 1
    ws.append([intitule for intitule, _ in colonnes])
    for cellule in ws[debut]:
        cellule.font = ENTETE
        cellule.fill = FOND_ENTETE
        cellule.alignment = Alignment(vertical="center")
    for ligne in lignes:
        ws.append(ligne)
        for (_, format_), cellule in zip(colonnes, ws[ws.max_row]):
            if format_:
                cellule.number_format = format_
            cellule.alignment = Alignment(vertical="top", wrap_text=isinstance(cellule.value, str) and len(cellule.value) > LARGEUR_MAX)

    ws.freeze_panes = ws.cell(row=debut + 1, column=1)
    if lignes:
        ws.auto_filter.ref = f"A{debut}:{get_column_letter(len(colonnes))}{ws.max_row}"
    for i, (intitule, format_) in enumerate(colonnes, start=1):
        valeurs = [len(intitule)] + [
            12 if format_ in (FORMAT_DATE, FORMAT_DATE_HEURE) else len(str(l[i - 1] or "")) for l in lignes
        ]
        ws.column_dimensions[get_column_letter(i)].width = min(max(valeurs) + 2, LARGEUR_MAX)


def _octets(classeur):
    sortie = BytesIO()
    classeur.save(sortie)
    return sortie.getvalue()


def _infos_promotion(promotion):
    return [
        f"{promotion.formation.tenant.nom} · {promotion.formation.nom}",
        f"Export du {timezone.localtime():%d/%m/%Y à %H:%M}",
    ]


# ─── Progression de la promotion ──────────────────────────────────────────────

def progression(promotion):
    d = donnees_promotion(promotion)
    classeur = Workbook()

    ws = classeur.active
    ws.title = "Progression"
    _feuille(
        ws,
        f"Progression — {promotion.nom}",
        _infos_promotion(promotion),
        [
            ("Apprenant", None), ("Email", None), ("Genre", None), ("Progression", FORMAT_PCT), ("Rendus", None),
            ("Attendus", None), ("Validés", None), ("Non validés", None), ("Retards", None), ("Dernier dépôt", None), ("Suivi", None), ("Signaux", None),
        ],
        [
            [
                a["nom"], a["email"], LIBELLES_GENRE.get(a.get("genre", ""), ""), a["progression_pct"] / 100, a["rendus"],
                a["attendus"], a.get("valides", 0), a.get("non_valides", 0), a["retards"],
                a["dernier_depot"], a["niveau"].capitalize(), " ; ".join(a["signaux"]),
            ]
            for a in d["apprenants"]
        ],
    )

    # Matrice apprenants × compétences du référentiel
    referentiel = list(
        _referentiel(promotion)
        .select_related("competence__module", "niveau")
        .order_by("competence__module__ordre", "competence__ordre", "niveau__ordre")
    )
    inscrits = sorted(
        (
            i.apprenant for i in InscriptionPromotion.objects.filter(
                Q(actif=True) | Q(fermee_par_cloture=True), promotion=promotion
            ).select_related("apprenant")
        ),
        key=_nom,
    )
    validations = {
        (apprenant_id, cn_id): date
        for apprenant_id, cn_id, date in CompetenceValidee.objects.filter(
            apprenant__in=inscrits, competence_niveau__in=referentiel
        ).values_list("apprenant_id", "competence_niveau_id", "date_validation")
    }
    ws = classeur.create_sheet("Compétences")
    _feuille(
        ws,
        f"Compétences acquises — {promotion.nom}",
        ["Date de validation de chaque compétence ; case vide : pas encore acquise."],
        [("Apprenant", None), ("Acquises", None)] + [
            (f"{cn.competence.module.nom} · {cn.competence.nom} ({cn.niveau.nom})", FORMAT_DATE)
            for cn in referentiel
        ],
        [
            [
                _nom(a),
                f"{sum(1 for cn in referentiel if (a.id, cn.id) in validations)} / {len(referentiel)}",
                *[_local(validations.get((a.id, cn.id))) for cn in referentiel],
            ]
            for a in inscrits
        ],
    )
    return _octets(classeur)


# ─── Suivi des livrables et des évaluations ───────────────────────────────────

def suivi(promotion):
    maintenant = timezone.now()
    assignations = sorted(
        _assignations_suivies(Q(brief__promotion=promotion)),
        key=lambda a: (a.brief.date_limite, a.brief.titre, a.id),
    )
    modules = dict(promotion.formation.modules.values_list("id", "nom"))
    lignes = []
    for a in assignations:
        brief = a.brief
        etat, dernier = _etat(a)
        premier = _premier_depot(a)
        evaluation = next(iter(a.evaluations.all()), None)  # la plus récente d'abord
        visees = {cn.id for cn in brief.competence_niveaux.all()}
        acquises = (
            {c.competence_niveau_id for c in evaluation.competences.all() if c.acquis} & visees if evaluation else set()
        )
        if a.groupe_id:
            cible = f"Groupe {a.groupe.nom}"
            emails = ", ".join(m.apprenant.email for m in a.groupe.membres.select_related("apprenant") if m.actif)
        else:
            cible, emails = _nom(a.apprenant), a.apprenant.email
        echu = brief.date_limite < maintenant
        en_retard = (premier.date_depot > brief.date_limite) if premier else echu
        lignes.append([
            brief.titre,
            modules.get(brief.module_id, ""),
            _local(brief.date_limite),
            cible,
            emails,
            len(a.livrables.all()),
            _local(premier.date_depot) if premier else None,
            _local(dernier.date_depot) if dernier else None,
            "Oui" if en_retard else "Non",
            LIBELLES_ETAT[etat] if (etat != NON_RENDU or echu) else "À rendre",
            _local(evaluation.date_creation) if evaluation else None,
            _nom(evaluation.evaluateur) if evaluation and evaluation.evaluateur else "",
            f"{len(acquises)} / {len(visees)}" if evaluation and visees else "",
            _texte_court(evaluation.commentaire) if evaluation else "",
        ])

    classeur = Workbook()
    ws = classeur.active
    ws.title = "Suivi"
    _feuille(
        ws,
        f"Suivi des livrables et des évaluations — {promotion.nom}",
        _infos_promotion(promotion),
        [
            ("Brief", None), ("Module", None), ("Échéance", FORMAT_DATE_HEURE), ("Rendu par", None),
            ("Email", None), ("Dépôts", None), ("Premier dépôt", FORMAT_DATE_HEURE),
            ("Dernier dépôt", FORMAT_DATE_HEURE), ("En retard", None), ("État", None),
            ("Évalué le", FORMAT_DATE_HEURE), ("Évaluateur", None), ("Compétences acquises", None),
            ("Commentaire", None),
        ],
        lignes,
    )
    return _octets(classeur)


def _texte_court(contenu_html):
    from activites.decrochage import _texte

    return _texte(contenu_html, 2000)


# ─── Membres de l'organisme ───────────────────────────────────────────────────

def membres(tenant):
    promotions = {}
    for apprenant_id, nom in InscriptionPromotion.objects.filter(tenant=tenant, actif=True).values_list(
        "apprenant_id", "promotion__nom"
    ):
        promotions.setdefault(apprenant_id, []).append(nom)
    for formateur_id, nom in FormateurPromotion.objects.filter(
        promotion__formation__tenant=tenant, promotion__actif=True
    ).values_list("formateur_id", "promotion__nom"):
        promotions.setdefault(formateur_id, []).append(nom)

    ordre = {MembreTenant.Role.ADMINISTRATEUR: 0, MembreTenant.Role.FORMATEUR: 1, MembreTenant.Role.APPRENANT: 2}
    membres = sorted(
        MembreTenant.objects.filter(tenant=tenant).select_related("utilisateur"),
        key=lambda m: (ordre.get(m.role, 3), m.utilisateur.nom.lower(), m.utilisateur.prenom.lower()),
    )

    classeur = Workbook()
    ws = classeur.active
    ws.title = "Membres"
    _feuille(
        ws,
        f"Membres — {tenant.nom}",
        [f"Export du {timezone.localtime():%d/%m/%Y à %H:%M}"],
        [
            ("Nom", None), ("Prénom", None), ("Email", None), ("Genre", None), ("Rôle", None), ("Statut", None),
            ("Membre depuis", FORMAT_DATE), ("Dernière connexion", FORMAT_DATE_HEURE), ("Promotions ouvertes", None),
        ],
        [
            [
                m.utilisateur.nom,
                m.utilisateur.prenom,
                m.utilisateur.email,
                m.utilisateur.get_genre_display(),
                m.get_role_display(),
                "Désactivé" if not m.actif else ("Actif" if m.utilisateur.actif else "Invitation en attente"),
                _local(m.date_ajout),
                _local(m.utilisateur.last_login),
                ", ".join(sorted(promotions.get(m.utilisateur_id, []))),
            ]
            for m in membres
        ],
    )
    return _octets(classeur)
