"""Health check endpoints for load balancers and probes."""

from django.db import connection
from django.http import HttpRequest, JsonResponse


def health_check(request: HttpRequest) -> JsonResponse:
    """Return 200 when app+DB are reachable, 503 otherwise."""
    db_ok = True
    try:
        connection.ensure_connection()
    except Exception:
        db_ok = False

    status = 200 if db_ok else 503
    return JsonResponse(
        {"status": "ok" if db_ok else "degraded", "database": db_ok},
        status=status,
    )
