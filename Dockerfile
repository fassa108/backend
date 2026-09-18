FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Installation de uv
RUN pip install --no-cache-dir uv

# Copier les fichiers de gestion des dépendances
COPY pyproject.toml uv.lock ./

# Installer les dépendances du projet
RUN uv sync --frozen

# Copier le reste du projet
COPY . .

# Copier et rendre exécutable l'entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

CMD ["uv", "run", "python", "manage.py", "runserver", "0.0.0.0:8000"]