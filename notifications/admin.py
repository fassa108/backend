from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("titre", "utilisateur", "tenant", "type", "lue", "date_creation")
    list_filter = ("type", "lue", "tenant")
    search_fields = ("titre", "utilisateur__email")
