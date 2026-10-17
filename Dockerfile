FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Version utilisée par uv (sans elle, il prend la plus récente, parfois sans paquets précompilés)
ENV UV_PYTHON=3.14

# Bibliothèques système de WeasyPrint (rapports PDF) et polices
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

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