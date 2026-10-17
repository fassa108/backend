"""
Rapports générés par l'IA : rapport de promotion et bilan individuel.

Les chiffres sont calculés par Django au moment de la demande et figés
dans « donnees » (tableaux du PDF) ; l'IA rédige seulement le texte
(« contenu », en markdown), que le formateur relit et modifie avant de le
valider. Un rapport validé n'est plus modifiable.
"""

from django.conf import settings
from django.db import models


class Rapport(models.Model):
    class Type(models.TextChoices):
        PROMOTION = "PROMOTION", "Rapport de promotion"
        APPRENANT = "APPRENANT", "Bilan individuel"

    class Statut(models.TextChoices):
        EN_COURS = "EN_COURS", "En cours de génération"
        BROUILLON = "BROUILLON", "Brouillon"
        VALIDE = "VALIDE", "Validé"
        ECHEC = "ECHEC", "Échec"

    type = models.CharField(max_length=10, choices=Type.choices)
    promotion = models.ForeignKey(
        "pedagogie.Promotion",
        on_delete=models.CASCADE,
        related_name="rapports",
    )
    # Bilan individuel seulement
    apprenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="bilans",
    )
    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rapports_demandes",
    )
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.EN_COURS)
    # Chiffres et tableaux au moment de la demande (donnees.py)
    donnees = models.JSONField(default=dict, blank=True)
    contenu = models.TextField(blank=True)
    erreur = models.CharField(max_length=255, blank=True)
    valide_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rapports_valides",
    )
    date_validation = models.DateTimeField(null=True, blank=True)
    # Bilan validé, visible par l'apprenant
    partage = models.BooleanField(default=False)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_creation", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(type="PROMOTION", apprenant__isnull=True)
                    | models.Q(type="APPRENANT", apprenant__isnull=False)
                ),
                name="rapport_apprenant_selon_type",
            ),
        ]

    def __str__(self):
        return f"{self.get_type_display()} : {self.promotion} ({self.get_statut_display()})"
