"""
Django settings for fuel_route_planner project.
"""

import os
from pathlib import Path

# Load .env if present (no-op when python-dotenv is absent or file missing)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-1^*ff%gx06iwajqutdvqv_*r#9ej(7czd#k7@musx&e^+_p25b",
)

DEBUG = os.environ.get("DEBUG", "True").lower() not in ("false", "0", "no")

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

# Render automatically injects RENDER_EXTERNAL_HOSTNAME — add it so Django accepts requests.
_render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if _render_host and _render_host not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_render_host)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "routing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # serve static files (Django admin)
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "fuel_route_planner.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "fuel_route_planner.wsgi.application"

# ── Database ──────────────────────────────────────────────────────────────────
# Priority:
#   1. DATABASE_URL  — full postgres:// URL (Render internal URL, Railway, etc.)
#   2. DB_HOST       — individual host/name/user/password/port vars
#   3. SQLite        — local dev fallback when neither is set
_database_url = os.environ.get("DATABASE_URL", "")
_db_host = os.environ.get("DB_HOST", "")

if _database_url:
    from urllib.parse import urlparse as _urlparse
    _u = _urlparse(_database_url)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _u.path.lstrip("/"),
            "USER": _u.username,
            "PASSWORD": _u.password,
            "HOST": _u.hostname,
            "PORT": _u.port or 5432,
        }
    }
elif _db_host:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "fuel_route_planner"),
            "USER": os.environ.get("DB_USER", "postgres"),
            "PASSWORD": os.environ.get("DB_PASSWORD", "postgres"),
            "HOST": _db_host,
            "PORT": os.environ.get("DB_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# ── Routing engines ───────────────────────────────────────────────────────────
# GraphHopper free tier (500 req/day). Sign up at https://www.graphhopper.com/
# If blank the service automatically falls back to the public OSRM server.
GRAPHHOPPER_API_KEY = os.environ.get("GRAPHHOPPER_API_KEY", "")

# ── Fuel data ─────────────────────────────────────────────────────────────────
# Real station coordinates (no prices — prices come from EIA API).
FUEL_STATIONS_JSON_PATH = os.environ.get(
    "FUEL_STATIONS_JSON_PATH", str(BASE_DIR / "data" / "stations.json")
)

# EIA Open Data API key — free sign-up at https://www.eia.gov/opendata/register.php
# Used by `python manage.py sync_fuel_prices` to fetch weekly state-level gas prices.
EIA_API_KEY = os.environ.get("EIA_API_KEY", "")

# ── Cache ─────────────────────────────────────────────────────────────────────
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fuel-route-planner-cache",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
