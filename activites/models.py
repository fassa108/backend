from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q

from pedagogie.models import (
    CompetenceNiveau,
    Groupe,
    InscriptionPromotion,
    Module,
    Promotion,
)

# Extensions autorisées pour les fichiers livrables
EXTENSIONS_LIVRABLES = ["pdf", "pptx", "docx", "txt"]

# Extensions autorisées pour les ressources
EXTENSIONS_RESSOURCES = ["pdf", "pptx", "docx", "txt"]


class Ressource(models.Model):
    """
    Ressource de la bibliothèque de l'organisme (fichier ou lien).

    Créée par un formateur ou un administrateur ; on la joint ensuite à
    un ou plusieurs briefs. Seuls son créateur et l'admin d'organisme la
    modifient ou la suppriment.

    Contient soit une URL, soit un fichier — jamais les deux.
    """

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="ressources",
    )

    formateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ressources_creees",
    )

    titre = models.CharField(max_length=255)

    url = models.URLField(
        blank=True,
        null=True,
    )

    fichier = models.FileField(
        upload_to="ressources/",
        blank=True,
        null=True,
        validators=[
            FileExtensionValidator(
                allowed_extensions=EXTENSIONS_RESSOURCES,
            )
        ],
    )

    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_creation"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(url__isnull=False, url__gt="", fichier="")
                    | Q(url__isnull=True, fichier__gt="")
                ),
                name="ressource_exactement_une_source",
            ),
        ]

    def __str__(self):
        return self.titre


class Brief(models.Model):
    """
    Activité proposée à une promotion.

    Le brief appartient à la promotion (et non au formateur) : s'il est
    retiré de la promotion, ses briefs restent gérés par les autres
    formateurs. « cree_par » garde la trace de l'auteur.
    """

    class Statut(models.TextChoices):
        BROUILLON = "BROUILLON", "Brouillon"
        PUBLIE = "PUBLIE", "Publié"
        ARCHIVE = "ARCHIVE", "Archivé"

    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.CASCADE,
        related_name="briefs",
    )
    # Module principal du brief (obligatoire)
    module = models.ForeignKey(
        Module,
        on_delete=models.PROTECT,
        related_name="briefs",
    )
    cree_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="briefs_crees",
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

    # Compétences visées, chacune à un niveau (peuvent venir d'autres
    # modules de la formation que le module principal)
    competence_niveaux = models.ManyToManyField(
        CompetenceNiveau,
        related_name="briefs",
        blank=True,
    )

    # Ressources de la bibliothèque de l'organisme jointes au brief
    ressources = models.ManyToManyField(
        "Ressource",
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
                # Au moins une cible doit être renseignée :
                # apprenant seul, groupe seul, ou les deux ensemble.
                # Seul le cas "ni apprenant ni groupe" est interdit.
                condition=(
                    Q(groupe__isnull=False) | Q(apprenant__isnull=False)
                ),
                name="assignation_au_moins_une_cible",
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


class Livrable(models.Model):
    class Statut(models.TextChoices):
        SOUMIS = "SOUMIS", "Soumis"
        INVALIDE = "INVALIDE", "Invalide"
        RETENU = "RETENU", "Retenu"

    assignation = models.ForeignKey(
        Assignation,
        on_delete=models.CASCADE,
        related_name="livrables",
    )
    deposant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="livrables_deposes",
    )
    titre = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.SOUMIS,
    )
    date_depot = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.titre


class FichierLivrable(models.Model):
    livrable = models.ForeignKey(
        Livrable,
        on_delete=models.CASCADE,
        related_name="fichiers",
    )
    nom = models.CharField(max_length=255)
    fichier = models.FileField(
        upload_to="livrables/",
        blank=True,
        null=True,
        validators=[
            FileExtensionValidator(
                allowed_extensions=EXTENSIONS_LIVRABLES,
            )
        ],
    )
    url = models.URLField(
        blank=True,
        null=True,
    )
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    (
                        Q(url__isnull=False)
                        & ~Q(url="")
                        & (Q(fichier__isnull=True) | Q(fichier=""))
                    )
                    |
                    (
                        Q(fichier__isnull=False)
                        & ~Q(fichier="")
                        & (Q(url__isnull=True) | Q(url=""))
                    )
                ),
                name="fichier_livrable_exactement_une_source",
            )
        ]

    def __str__(self):
        return self.nom
