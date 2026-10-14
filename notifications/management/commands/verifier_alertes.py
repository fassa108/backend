from django.core.management.base import BaseCommand

from notifications.tasks import verifier_alertes


class Command(BaseCommand):
    help = "Lance tout de suite la vérification quotidienne des alertes (sans attendre Celery beat)."

    def handle(self, *args, **options):
        resultat = verifier_alertes()
        self.stdout.write(self.style.SUCCESS(f"Vérification terminée : {resultat}"))
