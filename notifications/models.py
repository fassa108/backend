"""
Notifications affichées dans l'application (cloche du header).

Les emails restent envoyés par les tâches de chaque app : une
notification en est l'équivalent visible dans l'interface.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q


class Notification(models.Model):
    class Type(models.TextChoices):
        ASSIGNATION = "ASSIGNATION", "Brief assigné"
        DEPOT = "DEPOT", "Dépôt reçu"
        EVALUATION = "EVALUATION", "Rendu évalué"
        COMMENTAIRE = "COMMENTAIRE", "Commentaire"
        COMPETENCE = "COMPETENCE", "Compétence validée"
        RAPPEL = "RAPPEL", "Rappel"
        ALERTE = "ALERTE", "Alerte"
        INSCRIPTION = "INSCRIPTION", "Inscription"
        ABONNEMENT = "ABONNEMENT", "Abonnement"
        IA = "IA", "Assistant IA"

    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    # Vide pour les notifications de l'admin SaaS (hors organisme)
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
    )
    type = models.CharField(max_length=20, choices=Type.choices)
    titre = models.CharField(max_length=150)
    message = models.CharField(max_length=500, blank=True)
    # Chemin du frontend ouvert au clic, ex. « /activites/12 »
    lien = models.CharField(max_length=255, blank=True)
    # Anti-doublon des alertes recalculées chaque jour, ex. « rappel-brief-12-J2 »
    cle = models.CharField(max_length=100, blank=True)
    lue = models.BooleanField(default=False)
    date_lecture = models.DateTimeField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_creation", "-id"]
        indexes = [
            models.Index(fields=["utilisateur", "lue", "-date_creation"], name="notif_utilisateur_lue_date"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["utilisateur", "cle"],
                condition=~Q(cle=""),
                name="unique_notification_par_cle",
            ),
        ]

    def __str__(self):
        return f"{self.utilisateur} : {self.titre}"
