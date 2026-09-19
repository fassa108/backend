from django.contrib import admin

from .models import (
    Competence,
    CompetenceNiveau,
    Formation,
    Module,
    Niveau,
    Promotion,
)


@admin.register(Formation)
class FormationAdmin(admin.ModelAdmin):
    list_display = ("nom", "tenant", "actif", "date_creation")
    list_filter = ("actif", "tenant")
    search_fields = ("nom", "description")


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    list_display = ("nom", "formation", "date_debut", "date_fin", "actif")
    list_filter = ("actif", "formation__tenant")
    search_fields = ("nom", "description")


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ("nom", "formation", "ordre", "actif")
    list_filter = ("actif", "formation__tenant")
    search_fields = ("nom", "description")


@admin.register(Competence)
class CompetenceAdmin(admin.ModelAdmin):
    list_display = ("nom", "module", "ordre", "actif")
    list_filter = ("actif", "module__formation__tenant")
    search_fields = ("nom", "description")


@admin.register(Niveau)
class NiveauAdmin(admin.ModelAdmin):
    list_display = ("nom", "tenant", "ordre", "actif", "date_creation")
    list_filter = ("actif", "tenant")
    search_fields = ("nom", "description")


@admin.register(CompetenceNiveau)
class CompetenceNiveauAdmin(admin.ModelAdmin):
    list_display = ("competence", "niveau", "date_creation")
    list_filter = ("niveau__tenant",)
    search_fields = ("competence__nom", "niveau__nom", "description")

