"""
Quiz et fiches de révision générés par IA, rattachés à un module.

Le formateur choisit des sources (ressources, livrables de ses promotions,
fichiers ajoutés) ; le service IA (eduhub_fastapi) génère le contenu via
une tâche Celery ; le formateur relit, corrige puis publie. Les apprenants
qui ont déposé sur un brief du module voient les supports publiés.
"""

import os
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from activites.models import FichierLivrable, Ressource
from pedagogie.models import Module

# Par module (toutes promotions confondues) : un quiz par difficulté, deux fiches
NB_FICHES_MAX = 2
# Fichiers envoyés au service IA pour une génération
NB_SOURCES_MAX = 5
NB_QUESTIONS_MAX = 20


def chemin_source(instance, nom_original):
    ext = os.path.splitext(nom_original)[1].lower()
    tenant_id = instance.support.module.formation.tenant_id
    return f"revision/{tenant_id}/{uuid.uuid4().hex}{ext}"


class SupportRevision(models.Model):
    """Un quiz ou une fiche de révision, de sa génération à sa publication."""

    class Type(models.TextChoices):
        QUIZ = "QUIZ", "Quiz"
        FICHE = "FICHE", "Fiche de révision"

    class Difficulte(models.TextChoices):
        FACILE = "FACILE", "Facile"
        MOYEN = "MOYEN", "Moyen"
        DIFFICILE = "DIFFICILE", "Difficile"

    class Statut(models.TextChoices):
        EN_COURS = "EN_COURS", "Génération en cours"
        ECHEC = "ECHEC", "Échec de la génération"
        BROUILLON = "BROUILLON", "À relire"
        PUBLIE = "PUBLIE", "Publié"

    # RESTRICT, comme pour les briefs : un module utilisé ne se supprime pas seul
    module = models.ForeignKey(
        Module,
        on_delete=models.RESTRICT,
        related_name="supports_revision",
    )
    type = models.CharField(max_length=10, choices=Type.choices)
    # Quiz uniquement
    difficulte = models.CharField(max_length=10, choices=Difficulte.choices, blank=True)
    nb_questions = models.PositiveSmallIntegerField(null=True, blank=True)

    titre = models.CharField(max_length=255, blank=True)
    # Fiche uniquement : {titre, resume, sections: [{titre, points}], a_retenir}
    contenu = models.JSONField(null=True, blank=True)

    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.EN_COURS)
    erreur = models.TextField(blank=True)

    cree_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supports_revision_crees",
    )

    # Sources choisies (les fichiers ajoutés sont dans FichierSource)
    ressources = models.ManyToManyField(Ressource, related_name="supports_revision", blank=True)
    fichiers_livrables = models.ManyToManyField(
        FichierLivrable, related_name="supports_revision", blank=True
    )

    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)
    date_publication = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["type", "difficulte", "date_creation"]
        constraints = [
            models.UniqueConstraint(
                fields=["module", "difficulte"],
                condition=Q(type="QUIZ"),
                name="un_quiz_par_difficulte_et_module",
            ),
            models.CheckConstraint(
                condition=(
                    Q(type="QUIZ", difficulte__in=["FACILE", "MOYEN", "DIFFICILE"])
                    | Q(type="FICHE", difficulte="")
                ),
                name="support_difficulte_selon_type",
            ),
        ]

    @property
    def est_quiz(self):
        return self.type == self.Type.QUIZ

    def __str__(self):
        if self.est_quiz:
            return f"Quiz {self.get_difficulte_display().lower()} : {self.module}"
        return f"Fiche : {self.titre or self.module}"


class FichierSource(models.Model):
    """Fichier ajouté pour une génération ; effacé une fois la génération terminée."""

    support = models.ForeignKey(
        SupportRevision,
        on_delete=models.CASCADE,
        related_name="fichiers_ajoutes",
    )
    nom = models.CharField(max_length=255)
    fichier = models.FileField(upload_to=chemin_source)

    def __str__(self):
        return self.nom


class Question(models.Model):
    class Type(models.TextChoices):
        CHOIX_UNIQUE = "CHOIX_UNIQUE", "Choix unique"
        CHOIX_MULTIPLE = "CHOIX_MULTIPLE", "Choix multiple"

    support = models.ForeignKey(
        SupportRevision,
        on_delete=models.CASCADE,
        related_name="questions",
    )
    ordre = models.PositiveSmallIntegerField()
    intitule = models.TextField()
    type = models.CharField(max_length=20, choices=Type.choices)
    explication = models.TextField(blank=True)

    class Meta:
        ordering = ["ordre", "id"]

    def __str__(self):
        return self.intitule[:80]


class Option(models.Model):
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="options",
    )
    ordre = models.PositiveSmallIntegerField()
    texte = models.TextField()
    est_correcte = models.BooleanField(default=False)

    class Meta:
        ordering = ["ordre", "id"]

    def __str__(self):
        return self.texte[:80]


class Tentative(models.Model):
    """Un passage d'un apprenant sur un quiz publié (autant qu'il veut)."""

    support = models.ForeignKey(
        SupportRevision,
        on_delete=models.CASCADE,
        related_name="tentatives",
    )
    apprenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tentatives_quiz",
    )
    # {"<id question>": [<id option>, …]}
    reponses = models.JSONField(default=dict)
    score = models.PositiveSmallIntegerField()
    total = models.PositiveSmallIntegerField()
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"{self.apprenant} : {self.score}/{self.total}"
