import re
import unicodedata

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