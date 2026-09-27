from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from tenants.models import Tenant


class Formation(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="formations",
    )
    nom = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "nom"],
                name="unique_formation_par_tenant",
            ),
        ]
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class Promotion(models.Model):
    formation = models.ForeignKey(
        Formation,
        on_delete=models.CASCADE,
        related_name="promotions",
    )
    nom = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    date_debut = models.DateField()
    date_fin = models.DateField(null=True, blank=True)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["formation", "nom"],
                name="unique_promotion_par_formation",
            ),
            models.CheckConstraint(
                condition=Q(date_fin__isnull=True) | Q(date_fin__gte=F("date_debut")),
                name="check_promotion_dates",
            ),
        ]
        ordering = ["-date_debut"]

    def clean(self):
        super().clean()
        if self.date_debut and self.date_fin and self.date_fin < self.date_debut:
            raise ValidationError(
                {"date_fin": "La date de fin doit être postérieure ou égale à la date de début."}
            )

    def __str__(self):
        return f"{self.formation.nom} - {self.nom}"


class Module(models.Model):
    formation = models.ForeignKey(
        Formation,
        on_delete=models.CASCADE,
        related_name="modules",
    )
    nom = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    ordre = models.PositiveIntegerField(default=1)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["formation", "nom"],
                name="unique_module_par_formation",
            ),
            models.UniqueConstraint(
                fields=["formation", "ordre"],
                name="unique_ordre_module_par_formation",
            ),
            models.CheckConstraint(
                condition=Q(ordre__gte=1),
                name="check_module_ordre_positif",
            ),
        ]
        ordering = ["ordre", "nom"]

    def __str__(self):
        return f"{self.formation.nom} - {self.nom}"


class Competence(models.Model):
    module = models.ForeignKey(
        Module,
        on_delete=models.CASCADE,
        related_name="competences",
    )
    nom = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    ordre = models.PositiveIntegerField(default=1)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["module", "nom"],
                name="unique_competence_par_module",
            ),
            models.UniqueConstraint(
                fields=["module", "ordre"],
                name="unique_ordre_competence_par_module",
            ),
            models.CheckConstraint(
                condition=Q(ordre__gte=1),
                name="check_competence_ordre_positif",
            ),
        ]
        ordering = ["ordre", "nom"]

    def __str__(self):
        return f"{self.module.nom} - {self.nom}"


class Niveau(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="niveaux",
    )
    nom = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    ordre = models.PositiveIntegerField(default=1)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "nom"],
                name="unique_niveau_par_tenant",
            ),
            models.UniqueConstraint(
                fields=["tenant", "ordre"],
                name="unique_ordre_niveau_par_tenant",
            ),
            models.CheckConstraint(
                condition=Q(ordre__gte=1),
                name="check_niveau_ordre_positif",
            ),
        ]
        ordering = ["ordre", "nom"]

    def __str__(self):
        return self.nom


class CompetenceNiveau(models.Model):
    competence = models.ForeignKey(
        Competence,
        on_delete=models.CASCADE,
        related_name="niveaux",
    )
    niveau = models.ForeignKey(
        Niveau,
        on_delete=models.CASCADE,
        related_name="competences",
    )
    description = models.TextField(blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["competence", "niveau"],
                name="unique_competence_niveau",
            ),
        ]
        ordering = ["niveau__ordre", "competence__ordre"]

    def clean(self):
        super().clean()
        if self.competence_id and self.niveau_id:
            if self.competence.module.formation.tenant_id != self.niveau.tenant_id:
                raise ValidationError(
                    "La compétence et le niveau doivent appartenir au même organisme."
                )

    def __str__(self):
        return f"{self.competence.nom} - {self.niveau.nom}"


class InscriptionPromotion(models.Model):
    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.CASCADE,
        related_name="inscriptions",
    )
    apprenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="inscriptions_promotions",
    )
    actif = models.BooleanField(default=True)
    date_inscription = models.DateTimeField(auto_now_add=True)
    date_desinscription = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["apprenant"],
                condition=Q(actif=True),
                name="unique_promotion_active_par_apprenant",
            ),
            models.UniqueConstraint(
                fields=["promotion", "apprenant"],
                name="unique_apprenant_par_promotion",
            ),
        ]
        ordering = ["-date_inscription"]

    def clean(self):
        super().clean()
        if self.apprenant_id and self.promotion_id:
            from accounts.models import MembreTenant
            tenant = self.promotion.formation.tenant
            if not MembreTenant.objects.filter(
                utilisateur_id=self.apprenant_id,
                tenant=tenant,
                role=MembreTenant.Role.APPRENANT,
                actif=True,
            ).exists():
                raise ValidationError(
                    {"apprenant": "L'utilisateur doit être un apprenant actif de cet organisme."}
                )

    def __str__(self):
        return f"{self.apprenant} - {self.promotion.nom}"


class Groupe(models.Model):
    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.CASCADE,
        related_name="groupes",
    )
    nom = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    apprenants = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="GroupeMembre",
        related_name="groupes_pedagogiques",
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["promotion", "nom"],
                name="unique_groupe_par_promotion",
            ),
        ]
        ordering = ["nom"]

    def __str__(self):
        return f"{self.promotion.nom} - {self.nom}"


class GroupeMembre(models.Model):
    groupe = models.ForeignKey(
        Groupe,
        on_delete=models.CASCADE,
        related_name="membres",
    )
    apprenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="appartenances_groupes",
    )
    date_ajout = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["groupe", "apprenant"],
                name="unique_apprenant_par_groupe",
            ),
        ]
        ordering = ["-date_ajout"]

    def clean(self):
        super().clean()
        if self.groupe_id and self.apprenant_id:
            if not InscriptionPromotion.objects.filter(
                promotion_id=self.groupe.promotion_id,
                apprenant_id=self.apprenant_id,
                actif=True,
            ).exists():
                raise ValidationError(
                    {"apprenant": "L'apprenant doit avoir une inscription active dans la promotion de ce groupe."}
                )

    def __str__(self):
        return f"{self.groupe.nom} - {self.apprenant}"


class FormateurPromotion(models.Model):
    """
    Affectation d'un Formateur à une Promotion.

    Un formateur peut être affecté à plusieurs promotions.
    Une promotion peut avoir plusieurs formateurs.
    Un formateur ne peut être affecté qu'une seule fois à la même promotion.

    Seul l'Admin organisme peut gérer ces affectations.
    L'Admin SaaS n'a pas accès à cette opération.
    """

    formateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="promotions_formateur",
    )

    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.CASCADE,
        related_name="formateurs_affectes",
    )

    date_ajout = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["formateur", "promotion"],
                name="unique_formateur_par_promotion",
            ),
        ]
        ordering = ["-date_ajout"]

    def clean(self):
        super().clean()
        if self.formateur_id and self.promotion_id:
            from accounts.models import MembreTenant
            tenant = self.promotion.formation.tenant
            if not MembreTenant.objects.filter(
                utilisateur_id=self.formateur_id,
                tenant=tenant,
                role=MembreTenant.Role.FORMATEUR,
                actif=True,
            ).exists():
                raise ValidationError(
                    {
                        "formateur": (
                            "L'utilisateur sélectionné doit être un formateur actif "
                            "de cet organisme."
                        )
                    }
                )

    def __str__(self):
        return f"{self.formateur} → {self.promotion}"
