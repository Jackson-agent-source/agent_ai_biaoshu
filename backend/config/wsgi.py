"""WSGI 入口（同步部署用）。V1 用 `python manage.py runserver` 就足够了。"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()
