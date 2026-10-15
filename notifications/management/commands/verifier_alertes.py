from django.core.management.base import BaseCommand

from notifications.tasks import envoyer_rappels, verifier_alertes


class Command(BaseCommand):
    help = "Lance tout de suite les rappels et la vérification des alertes (sans attendre Celery beat)."

    def handle(self, *args, **options):
        resultat = {**envoyer_rappels(), **verifier_alertes()}
        self.stdout.write(self.style.SUCCESS(f"Vérification terminée : {resultat}"))
