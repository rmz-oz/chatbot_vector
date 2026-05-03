import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=True)

def config(key, default=None, cast=None):
    val = os.environ.get(key, default)
    if cast and val is not None:
        val = cast(val)
    return val

SECRET_KEY = config("SECRET_KEY", default="dev-secret-key-change-in-production")
DEBUG = config("DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="*").split(",")

INSTALLED_APPS = [
    "chat",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "scraper",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

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

# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME":     config("POSTGRES_DB",       default="chatbot"),
        "USER":     config("POSTGRES_USER",     default="chatbot"),
        "PASSWORD": config("POSTGRES_PASSWORD", default="chatbot"),
        "HOST":     config("POSTGRES_HOST",     default="db"),
        "PORT":     config("POSTGRES_PORT",     default="5432"),
    }
}

# Redis cache
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": config("REDIS_URL", default="redis://redis:6379/0"),
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        "TIMEOUT": 3600,
    }
}

# Session
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"

# Static files
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# Localisation
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Istanbul"
USE_I18N = False
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Ollama (local LLM)
OLLAMA_URL   = config("OLLAMA_URL",   default="http://ollama:11434")
OLLAMA_MODEL = config("OLLAMA_MODEL", default="llama3.2:3b")

# Embedding model (nomic-embed-text via Ollama — 768 dimensions)
EMBEDDING_MODEL = config("EMBEDDING_MODEL", default="nomic-embed-text")

# RAG
RAG_MAX_ENTRIES = config("RAG_MAX_ENTRIES", default=5, cast=int)

# Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "loggers": {
        "chat": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}
