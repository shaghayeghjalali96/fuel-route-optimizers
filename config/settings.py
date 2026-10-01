"""Django settings for the fuel-route assessment API."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "dev-only-change-me-for-assessment"

DEBUG = True

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "fuelroute",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
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
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Vehicle assumptions from the assignment
VEHICLE_MAX_RANGE_MILES = 500
VEHICLE_MPG = 10

# How far a station may sit from the driving route (city-center geocoding is coarse)
ROUTE_CORRIDOR_MILES = 12

# Prefer fueling before the tank is empty
FUEL_RESERVE_MILES = 40

# External services (free, no API key)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"

FUEL_CSV_PATH = BASE_DIR / "fuel-prices-for-be-assessment.csv"
US_CITIES_CSV_PATH = BASE_DIR / "data" / "us_cities.csv"

# File-based cache survives runserver reloads and is shared across workers
CACHE_DIR = BASE_DIR / ".cache"
CACHE_DIR.mkdir(exist_ok=True)

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": str(CACHE_DIR / "django"),
        "TIMEOUT": 60 * 60 * 6,
        "OPTIONS": {"MAX_ENTRIES": 5000},
    }
}
