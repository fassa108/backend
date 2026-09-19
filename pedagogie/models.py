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