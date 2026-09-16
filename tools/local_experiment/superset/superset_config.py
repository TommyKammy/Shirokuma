"""Configuration for the isolated, loopback-only local BI experiment."""
import json
from pathlib import Path

_private = json.loads(Path('/app/superset_home/local-private.json').read_text())
SECRET_KEY = _private['secret_key']
SQLALCHEMY_DATABASE_URI = 'sqlite:////app/superset_home/superset.db'
WTF_CSRF_ENABLED = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
TALISMAN_ENABLED = False  # Local HTTP frontend; no public listener or proxy.
ENABLE_PROXY_FIX = False
FEATURE_FLAGS = {'ENABLE_TEMPLATE_PROCESSING': False}
SQL_MAX_ROW = 1000
SQLLAB_TIMEOUT = 60
SUPERSET_WEBSERVER_TIMEOUT = 120
MAPBOX_API_KEY = ''
APP_NAME = 'Shirokuma Local BI'
