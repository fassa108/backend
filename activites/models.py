import os
import uuid

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

# Nombre maximal d'éléments (fichiers + liens) dans un dépôt
MAX_ELEMENTS_PAR_DEPOT = 10


def _nom_aleatoire(nom_original):
    """Nom de fichier sur disque : aléatoire, extension conservée (le vrai nom est en base)."""
    ext = os.path.splitext(nom_original)[1].lower()
    return f"{uuid.uuid4().hex}{ext}"


def chemin_ressource(instance, nom_original):
    return f"ressources/{instance.tenant_id}/{_nom_aleatoire(nom_original)}"


def chemin_fichier_livrable(instance, nom_original):
    brief = instance.livrable.assignation.brief
    tenant_id = brief.promotion.formation.tenant_id
    return f"livrables/{tenant_id}/{brief.id}/{_nom_aleatoire(nom_original)}"


def chemin_apercu(instance, nom_original):
    return f"apercus/{instance.tenant_id_fichier}/{_nom_aleatoire(nom_original)}"


class StatutApercu(models.TextChoices):
    """
    Aperçu PDF d'un fichier Office (docx, pptx), produit par Gotenberg.
    Vide : pas d'aperçu demandé (PDF, TXT, lien, ou conversion coupée).
    """
    EN_COURS = "EN_COURS", "En préparation"
    PRET = "PRET", "Prêt"
    ECHEC = "ECHEC", "Échec"


class AvecApercu(models.Model):
    """Champs communs aux modèles dont le fichier peut avoir un aperçu PDF."""

    apercu = models.FileField(upload_to=chemin_apercu, blank=True, null=True)
    apercu_statut = models.CharField(
        max_length=10,
        choices=StatutApercu.choices,
        blank=True,
        default="",
    )

    class Meta:
        abstract = True

    @property
    def extension(self):
        if not self.fichier:
            return ""
        return os.path.splitext(self.fichier.name)[1].lstrip(".").lower()


class Ressource(AvecApercu):
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
        upload_to=chemin_ressource,
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

    @property
    def tenant_id_fichier(self):
        return self.tenant_id

    @property
    def nom_affiche(self):
        return f"{self.titre}.{self.extension}" if self.extension else self.titre

    def __str__(self):
        return self.titre


class CategorieBrief(models.Model):
    """
    Catégorie d'activité propre à chaque organisme (ex. : Brief projet, TP,
    Atelier, Veille). Sert uniquement à classer : les règles du brief sont
    les mêmes quelle que soit la catégorie.
    """

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="categories_briefs",
    )
    nom = models.CharField(max_length=100)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "nom"],
                name="unique_categorie_brief_par_tenant",
            ),
        ]
        ordering = ["nom"]

    def __str__(self):
        return self.nom


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
    # Module principal du brief (obligatoire). RESTRICT : un module utilisé
    # ne peut pas être supprimé seul, mais la suppression en cascade de toute
    # la formation (qui supprime aussi ses briefs) reste possible.
    module = models.ForeignKey(
        Module,
        on_delete=models.RESTRICT,
        related_name="briefs",
    )
    cree_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="briefs_crees",
    )
    # Catégorie facultative (classement). RESTRICT : une catégorie utilisée
    # ne se supprime pas seule (on la désactive).
    categorie = models.ForeignKey(
        CategorieBrief,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="briefs",
    )
    titre = models.CharField(max_length=255)
    # Résumé court en texte simple (listes, cartes)
    description = models.TextField()

    # Sections en texte riche (HTML nettoyé, voir texte_riche.py).
    # Obligatoires : modalites_evaluation et livrables_attendus.
    contexte = models.TextField(blank=True)
    modalites_pedagogiques = models.TextField(blank=True)
    modalites_evaluation = models.TextField(blank=True)
    criteres_performance = models.TextField(blank=True)
    livrables_attendus = models.TextField(blank=True)

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
    """
    Un dépôt de l'apprenant sur une assignation (« Dépôt n°1, n°2… »).

    Plusieurs dépôts par assignation ; un dépôt n'est ni modifié ni
    supprimé (on en dépose un nouveau). Il contient un ou plusieurs
    éléments (FichierLivrable : fichier ou lien). Le retard est calculé
    (date_depot > date limite du brief), pas stocké.
    """

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
    # Numéro du dépôt dans l'assignation (1, 2, 3…)
    numero = models.PositiveIntegerField()
    commentaire = models.TextField(blank=True)
    date_depot = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_depot"]
        constraints = [
            models.UniqueConstraint(
                fields=["assignation", "numero"],
                name="unique_numero_depot_par_assignation",
            ),
        ]

    @property
    def en_retard(self):
        return self.date_depot > self.assignation.brief.date_limite

    def __str__(self):
        return f"Dépôt n°{self.numero}"


class FichierLivrable(AvecApercu):
    livrable = models.ForeignKey(
        Livrable,
        on_delete=models.CASCADE,
        related_name="fichiers",
    )
    # Nom affiché : nom d'origine du fichier, ou l'adresse du lien
    nom = models.CharField(max_length=255)
    fichier = models.FileField(
        upload_to=chemin_fichier_livrable,
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

    @property
    def tenant_id_fichier(self):
        return self.livrable.assignation.brief.promotion.formation.tenant_id

    @property
    def nom_affiche(self):
        return self.nom

    def __str__(self):
        return self.nom
