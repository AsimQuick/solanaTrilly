# ---
# module: core.views
# sprint: pre-sprint, sprint-10
# story: setup, US-48 AC-48.3
# status: implemented
# created-by: project-lead
# last-updated: 2026-06-17
# dependencies: django
# ---
from django.http import HttpResponse, JsonResponse


def health(request):
    """Liveness probe. Returns 200 with a small JSON body."""
    return JsonResponse({"status": "ok"})


def dashboard(request):
    """Dashboard SPA entry point — returns the React app shell (HTTP 200).

    In dev mode the Vite dev server proxies this; in production nginx serves
    the pre-built React assets. This Django view is the canonical dashboard
    route so the smoke-test can confirm HTTP 200 on the web container.
    """
    html = (
        "<!DOCTYPE html>"
        "<html lang='en'>"
        "<head><meta charset='UTF-8'><title>solanatrilly dashboard</title></head>"
        "<body><div id='root'></div></body>"
        "</html>"
    )
    return HttpResponse(html, content_type="text/html")
