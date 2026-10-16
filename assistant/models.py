"""
Assistant pédagogique de l'apprenant : conversations privées (le formateur
ne les voit pas), éventuellement rattachées à un brief.
"""

from django.conf import settings
from django.db import models

LONGUEUR_MAX_QUESTION = 1000


class Conversation(models.Model):
    apprenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversations_assistant",
    )
    promotion = models.ForeignKey(
        "pedagogie.Promotion",
        on_delete=models.CASCADE,
        related_name="conversations_assistant",
    )
    # Brief sur lequel porte la conversation (facultatif)
    brief = models.ForeignKey(
        "activites.Brief",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations_assistant",
    )
    titre = models.CharField(max_length=120, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_modification", "-id"]

    def __str__(self):
        return self.titre or f"Conversation #{self.pk}"


class Message(models.Model):
    class Role(models.TextChoices):
        APPRENANT = "APPRENANT", "Apprenant"
        ASSISTANT = "ASSISTANT", "Assistant"

    class Statut(models.TextChoices):
        PRET = "PRET", "Prêt"
        EN_COURS = "EN_COURS", "En cours"   # réponse de l'assistant attendue
        ECHEC = "ECHEC", "Échec"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10, choices=Role.choices)
    contenu = models.TextField(blank=True)
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.PRET)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date_creation", "id"]

    def __str__(self):
        return f"{self.get_role_display()} : {self.contenu[:40]}"
