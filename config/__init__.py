"""merge_pipeline project package initialization.

Registers PyMySQL as a MySQLdb drop-in so the Django MySQL backend works
without the C `mysqlclient` library, and imports the Celery app.
"""

import pymysql

pymysql.install_as_MySQLdb()

from config.celery import app as celery_app  # noqa: E402,F401
