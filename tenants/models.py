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

    Le formulaire crée la demande ; le paiement de l'abonnement (PayDunya)
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

    @property
    def paiement_reussi(self):
        return next((p for p in self.paiements.all() if p.statut == Paiement.Statut.REUSSI), None)


class Paiement(models.Model):
    """
    Tentative de paiement de l'abonnement via PayDunya : une facture créée
    chez PayDunya (« token »), payée sur leur page (mobile money, carte…).

    - Inscription : rattachée à la demande (l'organisme n'existe pas
      encore) ; l'organisme y est ajouté une fois créé.
    - Renouvellement : rattaché à l'organisme.

    Plusieurs tentatives possibles (annulée, puis réessayée) ; l'état est
    toujours relu chez PayDunya avant d'être enregistré (paydunya.py).
    """

    class Type(models.TextChoices):
        INSCRIPTION = "INSCRIPTION", "Inscription"
        RENOUVELLEMENT = "RENOUVELLEMENT", "Renouvellement"

    class Statut(models.TextChoices):
        EN_ATTENTE = "EN_ATTENTE", "En attente"
        REUSSI = "REUSSI", "Réussi"
        ANNULE = "ANNULE", "Annulé"
        ECHEC = "ECHEC", "Échec"

    class Moyen(models.TextChoices):
        # Paiements simulés d'avant PayDunya
        WAVE = "WAVE", "Wave"
        ORANGE_MONEY = "ORANGE_MONEY", "Orange Money"
        PAYDUNYA = "PAYDUNYA", "PayDunya"

    class Mode(models.TextChoices):
        TEST = "test", "Test"
        LIVE = "live", "Production"

    type = models.CharField(max_length=15, choices=Type.choices, default=Type.INSCRIPTION)
    demande = models.ForeignKey(
        DemandeInscription,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="paiements",
    )
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="paiements",
    )
    # Qui a lancé un renouvellement
    payeur = models.ForeignKey(
        "accounts.Utilisateur",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="paiements",
    )
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.EN_ATTENTE)
    # Sandbox ou production : les paiements de test ne comptent pas dans les revenus
    mode = models.CharField(max_length=4, choices=Mode.choices, default=Mode.TEST)
    moyen = models.CharField(max_length=20, choices=Moyen.choices, default=Moyen.PAYDUNYA)
    # Numéro du payeur, renvoyé par PayDunya une fois la facture payée
    telephone = models.CharField(max_length=30, blank=True)
    montant = models.PositiveIntegerField()  # en FCFA
    # Jeton de la facture PayDunya
    reference_transaction = models.CharField(max_length=100, unique=True)
    url_paiement = models.URLField(max_length=500, blank=True)
    url_recu = models.URLField(max_length=500, blank=True)
    # Période d'abonnement couverte, une fois le paiement réussi
    periode_debut = models.DateField(null=True, blank=True)
    periode_fin = models.DateField(null=True, blank=True)
    date_paiement = models.DateTimeField(auto_now_add=True)
    date_confirmation = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date_paiement", "-id"]

    def __str__(self):
        return f"{self.reference_transaction} : {self.montant} FCFA ({self.get_statut_display()})"


class Abonnement(models.Model):
    """
    Abonnement mensuel d'un organisme : payé jusqu'à « date_fin » (incluse).

    - Actif jusqu'à date_fin ; puis en grâce pendant JOURS_GRACE jours
      (accès maintenu, bannière) ; puis expiré : l'organisme est suspendu
      (abonnements.py, tâche quotidienne) jusqu'au paiement.
    - Chaque paiement prolonge d'un mois, à partir de date_fin si elle
      n'est pas passée (payer en avance ne fait pas perdre de jours).
    """

    JOURS_GRACE = 7

    class Statut(models.TextChoices):
        ACTIF = "ACTIF", "Actif"
        EN_GRACE = "EN_GRACE", "En période de grâce"
        EXPIRE = "EXPIRE", "Expiré"

    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name="abonnement")
    montant_mensuel = models.PositiveIntegerField()  # en FCFA
    date_debut = models.DateField()
    date_fin = models.DateField()
    # Suspendu par la tâche quotidienne faute de paiement (pas par l'admin SaaS) :
    # le paiement lève alors la suspension
    suspendu_pour_impaye = models.BooleanField(default=False)
    # L'admin SaaS a réactivé l'organisme malgré l'impayé : pas de nouvelle
    # suspension automatique pour cette échéance
    suspension_levee_pour = models.DateField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.tenant} : jusqu'au {self.date_fin:%d/%m/%Y}"

    @property
    def fin_grace(self):
        from datetime import timedelta

        return self.date_fin + timedelta(days=self.JOURS_GRACE)

    def statut_au(self, jour):
        if jour <= self.date_fin:
            return self.Statut.ACTIF
        if jour <= self.fin_grace:
            return self.Statut.EN_GRACE
        return self.Statut.EXPIRE
