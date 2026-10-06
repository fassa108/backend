import re
import unicodedata
import uuid

from django.db import models


class Tenant(models.Model):
    nom = models.CharField(
        max_length=150,
        unique=True
    )

    code = models.CharField(
        max_length=50,
        unique=True,
        editable=False
    )

    description = models.TextField(blank=True)

    email = models.EmailField(blank=True)
    telephone = models.CharField(max_length=30, blank=True)
    adresse = models.CharField(max_length=255, blank=True)
    site_web = models.URLField(blank=True)

    statut = models.BooleanField(default=True)

    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.code:
            # Translittération : « École Été » → « ECOLE ETE ».
            nom_ascii = (
                unicodedata.normalize("NFKD", self.nom)
                .encode("ascii", "ignore")
                .decode("ascii")
            )
            base = re.sub(
                r"[^A-Z0-9]+",
                "-",
                nom_ascii.upper()
            ).strip("-") or "ORGANISME"

            code = base[:40]
            compteur = 1

            while Tenant.objects.filter(
                code=code
            ).exclude(pk=self.pk).exists():
                code = f"{base[:35]}-{compteur}"
                compteur += 1

            self.code = code

        super().save(*args, **kwargs)

    def __str__(self):
        return self.nom


class DemandeInscription(models.Model):
    """
    Inscription d'un organisme depuis la page d'accueil.

    Le formulaire crée la demande ; le paiement de l'abonnement (simulé)
    la valide : l'organisme et son premier administrateur sont alors créés.
    La « reference » secrète donne accès à la page de paiement.
    """

    class Statut(models.TextChoices):
        EN_ATTENTE_PAIEMENT = "EN_ATTENTE_PAIEMENT", "En attente de paiement"
        PAYEE = "PAYEE", "Payée"

    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    nom_organisme = models.CharField(max_length=150)
    responsable_prenom = models.CharField(max_length=100)
    responsable_nom = models.CharField(max_length=100)
    email = models.EmailField()
    telephone = models.CharField(max_length=30, blank=True)
    message = models.TextField(max_length=1000, blank=True)

    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.EN_ATTENTE_PAIEMENT,
    )

    # Organisme créé au paiement
    tenant = models.ForeignKey(
        Tenant,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="demandes_inscription",
    )

    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_creation"]

    def __str__(self):
        return f"{self.nom_organisme} ({self.get_statut_display()})"


class Paiement(models.Model):
    """
    Paiement (simulé) de l'abonnement d'un organisme, par mobile money.
    Aucun prestataire n'est appelé : la transaction réussit toujours.
    """

    class Moyen(models.TextChoices):
        WAVE = "WAVE", "Wave"
        ORANGE_MONEY = "ORANGE_MONEY", "Orange Money"

    demande = models.OneToOneField(
        DemandeInscription,
        on_delete=models.PROTECT,
        related_name="paiement",
    )
    moyen = models.CharField(max_length=20, choices=Moyen.choices)
    telephone = models.CharField(max_length=20)
    montant = models.PositiveIntegerField()  # en FCFA
    reference_transaction = models.CharField(max_length=30, unique=True)
    date_paiement = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.reference_transaction} : {self.montant} FCFA ({self.get_moyen_display()})"
