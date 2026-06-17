# ---
# module: core.views
# sprint: pre-sprint, sprint-10
# story: setup, US-48 AC-48.3, US-49 AC-49.1, US-49 AC-49.2, US-49 AC-49.3
# status: implemented
# created-by: project-lead
# last-updated: 2026-06-17
# dependencies: django, core.dashboard.candle_api, core.dashboard.token_detail, core.tape.lake_reader,
#               core.schemas, core.resolver
# ---
from django.http import HttpResponse, JsonResponse

from core.dashboard.candle_api import build_candles
from core.dashboard.token_detail import build_token_detail
from core.schemas import DashboardConfig
from core.tape.lake_reader import LakeReader


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


def candle_api(request, mint: str):
    """Tape→candle API: derive OHLC candles for *mint* from the raw lake (AC-49.1).

    GET /api/candles/<mint>/?interval_s=<int>

    Principle #2 — ONE price basis: candles are derived from raw lake rows via
    build_candles(), which uses the same _lake_row_to_micro path as the shared
    US-30 FeatureExtractor.  There is NO candle-local price source.

    Principle #1 — config-driven intervals: the supported interval set comes
    from DashboardConfig.candle_intervals_s (get_active_config()) — never
    literals here.  Falls back to DashboardConfig defaults if no active config.

    Query params:
        interval_s (int, required): candle interval in seconds.
                   Must be in the config-driven supported-intervals set.

    Response (200):
        {
          "mint": "<mint>",
          "interval_s": <int>,
          "candles": [{"t": <int>, "open": <float>, "high": <float>,
                       "low": <float>, "close": <float>, "vol": <float>,
                       "interval_s": <int>}, ...]
        }

    Response (400): invalid/unsupported interval_s.
    """
    # 1. Resolve supported intervals from config (Principle #1 — no literals).
    try:
        from core.resolver import get_active_config  # noqa: PLC0415
        config = get_active_config()
        dash: DashboardConfig = config.dashboard if config is not None else DashboardConfig()
    except Exception:
        dash = DashboardConfig()
    supported: list[int] = dash.candle_intervals_s

    # 2. Parse and validate interval_s.
    raw_interval = request.GET.get("interval_s", "")
    if not raw_interval:
        return JsonResponse(
            {"error": f"interval_s is required; supported values: {supported}"},
            status=400,
        )
    try:
        interval_s = int(raw_interval)
    except ValueError:
        return JsonResponse(
            {"error": f"interval_s must be an integer; supported values: {supported}"},
            status=400,
        )
    if interval_s not in supported:
        return JsonResponse(
            {"error": f"interval_s={interval_s} not in supported set {supported}"},
            status=400,
        )

    # 3. Read raw lake rows — the SAME raw lake the FeatureExtractor reads from.
    #    No separate price fetch; price comes from row["price"] via build_candles.
    reader = LakeReader()
    rows = list(reader.iter_rows())

    # 4. Build OHLC candles via the shared extractor path (Principle #2).
    candles = build_candles(rows, mint, interval_s)

    return JsonResponse({"mint": mint, "interval_s": interval_s, "candles": candles})


def token_detail_api(request, mint: str):
    """Token-detail research view API (AC-49.2).

    GET /api/token-detail/<mint>/?interval_s=<int>

    Assembles the full token-detail payload from the raw lake (Principle #2 —
    one price basis, shared extractor path):
      candles  — OHLC series via build_candles (AC-49.1)
      overlay  — per-candle buy/sell-pressure & net-flow via build_overlay
      markers  — t0 (first swap) and score_time (t0 + ScoringConfig.score_at_elapsed_s)

    No firehose: all data comes from the lake (LakeReader) and config.

    Query params:
        interval_s (int, required): candle interval in seconds.
                   Must be in the config-driven supported-intervals set.

    Response (200):
        {
          "mint": str,
          "interval_s": int,
          "candles":  [{"t", "open", "high", "low", "close", "vol", "interval_s"}, ...],
          "overlay":  [{"t", "buy_vol", "sell_vol", "net_flow"}, ...],
          "markers":  {"t0": int | None, "score_time": int | None}
        }

    Response (400): invalid/unsupported interval_s.
    """
    # 1. Resolve supported intervals and scoring config (Principle #1).
    try:
        from core.resolver import get_active_config  # noqa: PLC0415
        config = get_active_config()
        dash: DashboardConfig = config.dashboard if config is not None else DashboardConfig()
        score_at_elapsed_s: int = (
            config.scoring.score_at_elapsed_s if config is not None else 120
        )
    except Exception:
        dash = DashboardConfig()
        score_at_elapsed_s = 120
    supported: list[int] = dash.candle_intervals_s

    # 2. Parse and validate interval_s.
    raw_interval = request.GET.get("interval_s", "")
    if not raw_interval:
        return JsonResponse(
            {"error": f"interval_s is required; supported values: {supported}"},
            status=400,
        )
    try:
        interval_s = int(raw_interval)
    except ValueError:
        return JsonResponse(
            {"error": f"interval_s must be an integer; supported values: {supported}"},
            status=400,
        )
    if interval_s not in supported:
        return JsonResponse(
            {"error": f"interval_s={interval_s} not in supported set {supported}"},
            status=400,
        )

    # 3. Read raw lake rows — the SAME raw lake as candle_api (Principle #2).
    reader = LakeReader()
    rows = list(reader.iter_rows())

    # 4. Assemble candles + overlay + markers via the shared assembler.
    detail = build_token_detail(rows, mint, interval_s, score_at_elapsed_s)

    return JsonResponse(detail)
