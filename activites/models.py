from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q

from pedagogie.models import (
    Competence,
    Groupe,
    InscriptionPromotion,
    Promotion,
)


class Brief(models.Model):
    class Statut(models.TextChoices):
        BROUILLON = "BROUILLON", "Brouillon"
        PUBLIE = "PUBLIE", "Publié"
        TERMINE = "TERMINE", "Terminé"
        ARCHIVE = "ARCHIVE", "Archivé"

    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.CASCADE,
        related_name="briefs",
    )
    titre = models.CharField(max_length=255)
    description = models.TextField()
    consignes = models.TextField()

    date_debut = models.DateTimeField()
    date_limite = models.DateTimeField()

    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.BROUILLON,
    )

    competences = models.ManyToManyField(
        Competence,
        related_name="briefs",
        blank=True,
    )

    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_creation"]
        constraints = [
            models.CheckConstraint(
                condition=Q(date_limite__gte=models.F("date_debut")),
                name="brief_date_limite_gte_date_debut",
            ),
        ]

    def __str__(self):
        return self.titre


class RessourceBrief(models.Model):
    brief = models.ForeignKey(
        Brief,
        on_delete=models.CASCADE,
        related_name="ressources",
    )
    titre = models.CharField(max_length=255)

    url = models.URLField(
        blank=True,
        null=True,
    )

    fichier = models.FileField(
        upload_to="briefs/ressources/",
        blank=True,
        null=True,
        validators=[
            FileExtensionValidator(
                allowed_extensions=["pdf", "txt"]
            )
        ],
    )

    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(url__isnull=False, fichier__isnull=True)
                    | Q(url__isnull=True, fichier__isnull=False)
                ),
                name="ressource_brief_exactement_une_source",
            ),
        ]

    def __str__(self):
        return self.titre


class Assignation(models.Model):
    brief = models.ForeignKey(
        Brief,
        on_delete=models.CASCADE,
        related_name="assignations",
    )

    groupe = models.ForeignKey(
        Groupe,
        on_delete=models.CASCADE,
        related_name="assignations",
        blank=True,
        null=True,
    )

    apprenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assignations",
        blank=True,
        null=True,
    )

    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(groupe__isnull=False, apprenant__isnull=True)
                    | Q(groupe__isnull=True, apprenant__isnull=False)
                ),
                name="assignation_exactement_une_cible",
            ),
            models.UniqueConstraint(
                fields=["brief", "groupe"],
                condition=Q(groupe__isnull=False),
                name="unique_assignation_brief_groupe",
            ),
            models.UniqueConstraint(
                fields=["brief", "apprenant"],
                condition=Q(apprenant__isnull=False),
                name="unique_assignation_brief_apprenant",
            ),
        ]

    def __str__(self):
        if self.groupe:
            return f"{self.brief} → Groupe {self.groupe}"
        return f"{self.brief} → Apprenant {self.apprenant}"