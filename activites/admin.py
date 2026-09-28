from django.contrib import admin

from .models import (
    Assignation,
    Brief,
    FichierLivrable,
    Livrable,
    Ressource,
)


@admin.register(Ressource)
class RessourceAdmin(admin.ModelAdmin):
    list_display = ("titre", "tenant", "formateur", "date_creation")
    list_filter = ("tenant",)
    search_fields = ("titre", "formateur__nom", "formateur__email")


@admin.register(Brief)
class BriefAdmin(admin.ModelAdmin):
    list_display = ("titre", "promotion", "module", "statut", "date_debut", "date_limite", "cree_par")
    list_filter = ("statut", "promotion__formation__tenant")
    search_fields = ("titre",)


@admin.register(Assignation)
class AssignationAdmin(admin.ModelAdmin):
    list_display = ("brief", "groupe", "apprenant", "date_creation")
    list_filter = ("brief__promotion__formation__tenant",)


@admin.register(Livrable)
class LivrableAdmin(admin.ModelAdmin):
    list_display = ("titre", "deposant", "statut", "date_depot")
    list_filter = ("statut",)
    search_fields = ("titre", "deposant__nom", "deposant__email")


@admin.register(FichierLivrable)
class FichierLivrableAdmin(admin.ModelAdmin):
    list_display = ("nom", "livrable", "date_creation")
    search_fields = ("nom",)
