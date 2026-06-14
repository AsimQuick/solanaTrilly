# ---
# module: core.views
# sprint: pre-sprint
# story: setup
# status: implemented
# created-by: project-lead
# last-updated: 2026-06-14
# dependencies: django
# ---
from django.http import JsonResponse


def health(request):
    """Liveness probe. Returns 200 with a small JSON body."""
    return JsonResponse({"status": "ok"})
