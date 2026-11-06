"""Studio Ops settings.

Everything environment-specific comes from environment variables, so the
same code runs on a laptop (SQLite) and in production (Vercel + Supabase Postgres).
"""
import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DEBUG", "0") == "1"
SECRET_KEY = os.environ.get("SECRET_KEY") or ("dev-only-insecure-key" if DEBUG else None)
if not SECRET_KEY:
    raise RuntimeError("Set the SECRET_KEY environment variable (see DEPLOY.md).")

# Vercel: "production", "preview" (pull requests / branches) or "development"; empty elsewhere.
VERCEL_ENV = os.environ.get("VERCEL_ENV", "")

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
# Vercel exposes the deployment's own hostnames as system environment variables.
for var in ("VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_URL", "VERCEL_BRANCH_URL"):
    if os.environ.get(var):
        ALLOWED_HOSTS.append(os.environ[var])
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1")]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "ops.apps.OpsConfig",
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
    "ops.middleware.SecurityHeadersMiddleware",
    "ops.middleware.PasswordAndSessionMiddleware",
    "ops.middleware.AuditMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "ops.context.nav",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database
# • Production: Supabase Postgres through its transaction pooler (port 6543), the
#   mode meant for serverless functions. The pooler can't keep prepared
#   statements, server-side cursors or long-lived connections, so all are off.
# • Pull-request previews never touch production data: unless PREVIEW_DATABASE_URL
#   is set they run on a throwaway demo database (see config/wsgi.py).
# • Local: SQLite.
if VERCEL_ENV == "preview":
    DATABASE_URL = os.environ.get("PREVIEW_DATABASE_URL", "")
else:
    DATABASE_URL = os.environ.get("DATABASE_URL", "")
PREVIEW_DEMO = VERCEL_ENV == "preview" and not DATABASE_URL

if DATABASE_URL:
    _db = dj_database_url.parse(DATABASE_URL, conn_max_age=0, ssl_require="sslmode=disable" not in DATABASE_URL
                                and "localhost" not in DATABASE_URL and "127.0.0.1" not in DATABASE_URL)
    if ":6543" in DATABASE_URL:                      # Supabase / PgBouncer transaction mode
        _db.setdefault("OPTIONS", {})["prepare_threshold"] = None
        _db["DISABLE_SERVER_SIDE_CURSORS"] = True
    else:
        _db["CONN_MAX_AGE"] = 60
        _db["CONN_HEALTH_CHECKS"] = True
elif PREVIEW_DEMO:
    _db = {"ENGINE": "django.db.backends.sqlite3", "NAME": "/tmp/studio-ops-preview.sqlite3"}
else:
    _db = {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}
DATABASES = {"default": _db}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Shared across all server processes (needed for the login lockout). Table is
# created by `manage.py createcachetable` in build.sh.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "ops_cache"}}

LANGUAGE_CODE = "en-in"
TIME_ZONE = os.environ.get("TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage" if not DEBUG
                    else "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

# Sessions: 7 days for the team (sliding), founders signed out after 2 idle hours.
SESSION_COOKIE_AGE = 60 * 60 * 24 * 7
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
ADMIN_IDLE_MINUTES = int(os.environ.get("ADMIN_IDLE_MINUTES", "120"))
PASSWORD_HASHERS = ["django.contrib.auth.hashers.ScryptPasswordHasher",
                    "django.contrib.auth.hashers.PBKDF2PasswordHasher"]

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "1") == "1"
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"
    # HSTS preload needs a domain you own; *.vercel.app isn't. Turn on with a custom domain.
    SECURE_HSTS_PRELOAD = os.environ.get("HSTS_PRELOAD", "0") == "1"
    if not SECURE_HSTS_PRELOAD:
        SILENCED_SYSTEM_CHECKS = ["security.W021"]

# Uploaded photos are compressed and stored in the database (serverless hosts
# have no disk). Vercel refuses request bodies over 4.5 MB, so the browser
# shrinks photos before sending (static/app.js) and the server matches that cap.
DATA_UPLOAD_MAX_MEMORY_SIZE = 4_500_000
FILE_UPLOAD_MAX_MEMORY_SIZE = 4_500_000
FILE_UPLOAD_TEMP_DIR = "/tmp" if VERCEL_ENV else None

# Vercel Cron calls /cron/daily/ with "Authorization: Bearer <CRON_SECRET>".
CRON_SECRET = os.environ.get("CRON_SECRET", "")

LOGGING = {
    "version": 1,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {"django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False}},
}

# Tests: fast password hashing (real hashing is deliberately slow).
import sys  # noqa: E402
if len(sys.argv) > 1 and sys.argv[1] == "test":
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
