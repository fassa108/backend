"""
Contexte pédagogique envoyé au service IA avec chaque question (anonymisé :
ni nom ni email) : la formation et son référentiel, le brief de la
conversation, ce que l'apprenant n'a pas encore acquis et les commentaires
de son formateur.
"""

from django.db.models import Q
from django.utils import timezone

from activites.decrochage import _texte
from activites.models import Assignation, CompetenceValidee, Evaluation
from activites.progression import _referentiel

from .models import Message

NB_HISTORIQUE = 10
NB_NON_ACQUISES = 20
NB_COMMENTAIRES = 5


def _libelle(cn):
    return f"{cn.competence.nom} ({cn.niveau.nom})"


def _assignation(apprenant, brief):
    return (
        Assignation.objects.filter(
            Q(apprenant=apprenant) | Q(groupe__membres__apprenant=apprenant, groupe__membres__actif=True),
            brief=brief,
        )
        .first()
    )


def contexte(conversation):
    apprenant, promotion, brief = conversation.apprenant, conversation.promotion, conversation.brief
    formation = promotion.formation
    referentiel = list(
        _referentiel(promotion)
        .select_related("competence__module", "niveau")
        .order_by("competence__module__ordre", "competence__ordre", "niveau__ordre")
    )
    modules = {}
    for cn in referentiel:
        modules.setdefault(cn.competence.module.nom, []).append(_libelle(cn))
    validees = set(CompetenceValidee.objects.filter(apprenant=apprenant).values_list("competence_niveau_id", flat=True))

    donnees = {
        "formation": formation.nom,
        "description_formation": _texte(formation.description, 2000),
        "modules": [{"nom": nom, "competences": competences[:60]} for nom, competences in modules.items()],
        "brief": None,
        "competences_non_acquises": [],
        "commentaires_formateur": [],
    }

    if brief is None:
        donnees["competences_non_acquises"] = [_libelle(cn) for cn in referentiel if cn.id not in validees][:NB_NON_ACQUISES]
        return donnees

    visees = list(brief.competence_niveaux.select_related("competence", "niveau"))
    assignation = _assignation(apprenant, brief)
    donnees["brief"] = {
        "titre": brief.titre,
        "module": brief.module.nom,
        "date_limite": timezone.localtime(brief.date_limite).strftime("%d/%m/%Y à %H:%M"),
        "rendu": bool(assignation and assignation.livrables.exists()),
        "description": _texte(brief.description, 4000),
        "contexte": _texte(brief.contexte, 4000),
        "modalites_pedagogiques": _texte(brief.modalites_pedagogiques),
        "modalites_evaluation": _texte(brief.modalites_evaluation),
        "criteres_performance": _texte(brief.criteres_performance),
        "livrables_attendus": _texte(brief.livrables_attendus),
        "competences_visees": [_libelle(cn) for cn in visees][:30],
    }
    donnees["competences_non_acquises"] = [_libelle(cn) for cn in visees if cn.id not in validees]
    if assignation:
        donnees["commentaires_formateur"] = [
            c for c in (
                _texte(t, 1000)
                for t in Evaluation.objects.filter(assignation=assignation)
                .exclude(commentaire="")
                .order_by("-date_creation")
                .values_list("commentaire", flat=True)[:NB_COMMENTAIRES]
            ) if c
        ]
    return donnees


def demande(question):
    """Requête pour /chat : contexte, échanges précédents réussis, et la question."""
    conversation = question.conversation
    precedents = list(
        conversation.messages.filter(statut=Message.Statut.PRET, id__lt=question.id)
        .exclude(contenu="")
        .order_by("-id")[:NB_HISTORIQUE]
    )
    return {
        "contexte": contexte(conversation),
        "historique": [
            {"role": "apprenant" if m.role == Message.Role.APPRENANT else "assistant", "contenu": m.contenu[:6000]}
            for m in reversed(precedents)
        ],
        "question": question.contenu,
    }
