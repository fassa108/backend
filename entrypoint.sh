#!/bin/sh
set -e

echo "======================================"
echo "Initialisation de Synapse"
echo "======================================"

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    echo "Application des migrations..."
    uv run python manage.py migrate --noinput
else
    echo "Migrations ignorées pour ce service."
fi

echo "Vérification de l'administrateur SaaS..."

if [ "${RUN_CREATE_SUPERUSER:-0}" = "1" ]; then

    uv run python manage.py shell <<'PY'
import os
from django.contrib.auth import get_user_model

User = get_user_model()

email = os.environ.get("DJANGO_SUPERUSER_EMAIL")
password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
nom = os.environ.get("DJANGO_SUPERUSER_NOM", "Admin")
prenom = os.environ.get("DJANGO_SUPERUSER_PRENOM", "SaaS")

if not email or not password:
    print(
        "DJANGO_SUPERUSER_EMAIL ou "
        "DJANGO_SUPERUSER_PASSWORD non défini."
    )
    print("Aucun administrateur SaaS ne sera créé.")

else:
    user = User.objects.filter(email=email).first()

    if user:
        print(f"L'administrateur SaaS {email} existe déjà.")

        modifications = []

        if not user.est_admin_saas:
            user.est_admin_saas = True
            modifications.append("est_admin_saas")

        if not user.actif:
            user.actif = True
            modifications.append("actif")

        if not user.is_staff:
            user.is_staff = True
            modifications.append("is_staff")

        if not user.is_superuser:
            user.is_superuser = True
            modifications.append("is_superuser")

        if modifications:
            user.save(update_fields=modifications)
            print("Compte administrateur SaaS mis à jour.")

    else:
        user = User(
            email=email,
            nom=nom,
            prenom=prenom,
            actif=True,
            est_admin_saas=True,
            is_staff=True,
            is_superuser=True,
        )

        user.set_password(password)
        user.save()

        print(f"Administrateur SaaS {email} créé.")

PY

else
    echo "Création de l'administrateur SaaS ignorée pour ce service."
fi

echo "Démarrage du service..."
exec "$@"