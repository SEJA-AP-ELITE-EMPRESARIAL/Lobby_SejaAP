# Backend do Lobby Seja AP — Django + gunicorn.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app

# libpq só é necessária para o psycopg em produção.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt

COPY . .

# O collectstatic roda no build com env forçado: sem isto ele leria o DEBUG e o
# SECRET_KEY do ambiente de build, que não existem, e falharia a imagem inteira.
# Só serve os estáticos do /django-admin/ — o lobby em si não tem build.
RUN mkdir -p /app/staticfiles \
    && DJANGO_DEBUG=false DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
