from django.contrib import admin

from .models import FichierSource, Option, Question, SupportRevision, Tentative


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0


@admin.register(SupportRevision)
class SupportRevisionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "type", "difficulte", "statut", "cree_par", "date_creation")
    list_filter = ("type", "statut", "module__formation__tenant")
    search_fields = ("titre", "module__nom")
    inlines = [QuestionInline]


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("intitule", "support", "type", "ordre")


admin.site.register(Option)
admin.site.register(Tentative)
admin.site.register(FichierSource)
