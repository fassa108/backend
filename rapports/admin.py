from django.contrib import admin

from .models import Rapport


@admin.register(Rapport)
class RapportAdmin(admin.ModelAdmin):
    list_display = ("__str__", "type", "promotion", "apprenant", "statut", "auteur", "date_creation")
    list_filter = ("type", "statut")
