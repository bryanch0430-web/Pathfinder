"""Proposal TC-02 (p4 l.140-143): the Japan set on live-shaped, fragmented provider data.

The proposal asks the Tokyo, Kyoto and Osaka scenarios to test fetching, parsing
and merging live, fragmented data from real APIs and web search. The sandbox
providers behind the rest of the japan run cannot do that, so every japan case
also runs this check.

Offline replay (the default, and the only mode CI runs)
    ``eval/fixtures/japan_replay.json`` holds, per city, the responses of every
    provider the app parses: Google Geocoding and Places (New), Open-Meteo
    geocoding, forecast and archive, Nominatim ``jsonv2`` with ``namedetails``,
    the Wikivoyage parse API and DeepSeek search. The rows are deliberately
    fragmented: missing fields, the same place under Japanese and English names
    in different sources (伏見稲荷大社 / Fushimi Inari Taisha), coordinates that
    disagree, and a per-case partial failure (an HTTP 5xx, a 429, an HTML error
    page, a missing forecast day). They are constructed from the parsers and the
    providers' public documentation; nothing was downloaded.

    The responses are served through the app's own HTTP helper
    (``app.infrastructure.http.use_transport``), so the real request code, status
    classification and parsers run. Four scores, each 0-1:

    * ``replay_parse``: each parser keeps the well-formed rows and drops the
      malformed ones, and a failed response becomes a classified
      ``ProviderError`` or an explicit "no result", never a crash or a made-up row;
    * ``replay_dedupe``: the agents' merge step (``agents._merge_places``) over
      all three place sources leaves one record per real place, and the plan the
      real pipeline builds has no stop twice;
    * ``replay_conflicts``: where three sources disagree on a place's
      coordinates, the merged point is the one two of them agree on;
    * ``replay_gaps``: the real ``PlanPath`` on the replayed registry records the
      injected failure (a data gap, an unavailable or seasonal weather day).

Live mode (opt-in, never in CI or the default test run)
    Set ``PATHFINDER_JAPAN_LIVE=1`` and the same steps run against the real
    providers: the keyless ones (Open-Meteo, Nominatim, Wikivoyage) always, Google
    and DeepSeek only when their keys are configured. No fault is injected, so
    ``replay_gaps`` is not scored; a source that is not configured is left out of
    ``replay_parse``; dedupe and conflicts use the same list of real places with
    a 1 km tolerance. Run it with::

        cd backend
        PATHFINDER_JAPAN_LIVE=1 python -m pytest -o addopts="" -q tests/test_japan_replay.py -k live

    or set the variable for the server and start a japan trial from the
    Evaluation page. The run report's ``model_snapshot["japan_replay"]`` says
    which mode produced the scores.
"""

from __future__ import annotations

import copy
import json
import os
import re
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import httpx

from app.application.agents import _merge_places, _norm_name
from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner
from app.application.planning import PlanPath
from app.application.prompts import PromptLibrary
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.errors import ProviderError
from app.domain.models import DataStatus, EvalCase, EvalCaseResult, EvalMetric, GeoLocation, GeoPoint, TripContext
from app.infrastructure.http import haversine_km, use_transport
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers import geocoding
from app.infrastructure.providers.geocoding import GoogleGeocoder, NominatimGeocoder, OpenMeteoGeocoder
from app.infrastructure.providers.places import GooglePlacesSource, NominatimPlacesSource, WikivoyagePlacesSource
from app.infrastructure.providers.registry import ProviderRegistry
from app.infrastructure.providers.weather import OpenMeteoArchiveWeather, OpenMeteoWeather
from app.infrastructure.providers.websearch import DeepSeekSearchSource

REPLAY_FILE = Path(__file__).resolve().parents[1] / "eval" / "fixtures" / "japan_replay.json"
LIVE_ENV = "PATHFINDER_JAPAN_LIVE"
SCORES = (
    ("replay_parse", "Replay: rows parsed as the source sent them"),
    ("replay_dedupe", "Replay: one record per real place"),
    ("replay_conflicts", "Replay: coordinate conflicts settled"),
    ("replay_gaps", "Replay: partial failures recorded"),
)
#: place routes, in the registry's priority order (Google, OSM, Wikivoyage)
PLACE_ROUTES = ("google.attractions", "nominatim.attractions", "wikivoyage")
CITY_KM = 15.0


def live_mode() -> bool:
    return os.environ.get(LIVE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def mode_label() -> str:
    return (
        "live keyless providers (PATHFINDER_JAPAN_LIVE)"
        if live_mode()
        else "offline replay of provider-shaped responses (eval/fixtures/japan_replay.json)"
    )


@lru_cache(maxsize=1)
def load_replay() -> dict[str, Any]:
    return json.loads(REPLAY_FILE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Replay transport: recorded-shape responses for the real HTTP helper
# ---------------------------------------------------------------------------


class Replay:
    """Serves a city's routes, with the case's faults applied, to ``request_json``."""

    def __init__(self, routes: list[dict[str, Any]], faults: list[dict[str, Any]] | None = None):
        self.routes = [copy.deepcopy(route) for route in routes]
        by_id = {route["id"]: route for route in self.routes}
        for route in self.routes:
            if "json_from" in route:  # a name search answers from the same rows as the list search
                route["json"] = by_id[route["json_from"]]["json"]
        for fault in faults or []:
            route = by_id[fault["route"]]
            route["fault"] = fault
        self.hits: Counter[str] = Counter()
        self.misses: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = f"{request.url.scheme}://{request.url.host}{request.url.path}"
        params = dict(request.url.params)
        try:
            body = json.loads(request.content) if request.content else {}
        except ValueError:
            body = {}
        for route in self.routes:
            if route["method"] != request.method or route["url"] != url:
                continue
            fields = {**(body if isinstance(body, dict) else {}), **params}
            if all(re.search(pattern, str(fields.get(key, "")), re.IGNORECASE) for key, pattern in (route.get("when") or {}).items()):
                self.hits[route["id"]] += 1
                return _respond(route, params, fields)
        self.misses.append(f"{request.method} {url}")
        return httpx.Response(404, json={"error": "no recorded response"})


def _respond(route: dict[str, Any], params: dict[str, str], fields: dict[str, Any] | None = None) -> httpx.Response:
    fault = route.get("fault") or {}
    status = int(fault.get("status", 200))
    if "text" in fault:
        return httpx.Response(status, text=fault["text"], headers={"content-type": "text/html; charset=utf-8"})
    body = copy.deepcopy(fault["json"] if "json" in fault else route["json"])
    if "daily_template" in route and status < 400:
        body["daily"] = _daily(route["daily_template"], params, fault)
    if route.get("filter"):
        body = _named_search(body, str((fields or {}).get(route["filter"], "")))
    return httpx.Response(status, json=body)


def _named_search(body: Any, query: str) -> Any:
    """A search by place name answers with the rows that carry every word of the name
    ("Ghibli Museum in Tokyo" -> rows naming the Ghibli Museum), as the real APIs do."""
    words = _norm_name(re.split(r"\s+in\s+", query, maxsplit=1)[0]).split()

    def text(row: dict[str, Any]) -> str:
        names = [str((row.get("displayName") or {}).get("text") or ""), str(row.get("name") or ""), str(row.get("display_name") or "")]
        names += [str(value) for value in (row.get("namedetails") or {}).values()]
        return _norm_name(" ".join(names))

    def keep(rows: list) -> list:
        return [row for row in rows if isinstance(row, dict) and all(word in text(row) for word in words)]

    if isinstance(body, list):
        return keep(body)
    if isinstance(body, dict) and isinstance(body.get("places"), list):
        body["places"] = keep(body["places"])
    return body


def _daily(template: dict[str, list], params: dict[str, str], fault: dict[str, Any]) -> dict[str, list]:
    """Open-Meteo `daily` arrays for the requested dates, minus the fault's dropped days."""
    start, end = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
    drop = set(fault.get("drop_days") or [])
    nulls = {key: set(indices) for key, indices in (fault.get("null") or {}).items()}
    out: dict[str, list] = {"time": [], **{key: [] for key in template}}
    for index in range((end - start).days + 1):
        if index in drop:
            continue
        out["time"].append((start + timedelta(days=index)).isoformat())
        for key, values in template.items():
            out[key].append(None if index in nulls.get(key, set()) else values[index % len(values)])
    return out


@contextmanager
def _private_geocode_cache() -> Iterator[None]:
    """Keep replayed coordinates out of the process-wide Google geocode cache."""
    saved = geocoding._GEOCODE_CACHE
    geocoding._GEOCODE_CACHE = {}
    try:
        yield
    finally:
        geocoding._GEOCODE_CACHE = saved


def replay_settings(settings: Settings, live: bool) -> Settings:
    update: dict[str, Any] = {
        "preplan_budget_seconds": 20,
        "agent_timeout_seconds": 10,
        "enable_notes": False,
        "enable_routes": False,
        "enable_route_matrix": False,
        "enable_timezone": False,
        "enable_elevation": False,
        "grok_search_enabled": False,
        "web_search_enabled": True,
        "langfuse_enabled": False,
    }
    if not live:
        # Placeholder keys switch the keyed parsers on; every request stays in the replay.
        update.update(
            {
                "google_places_api_key": "replay-key",
                "google_maps_api_key": "",
                "deepseek_search_api_key": "replay-key",
                "serpapi_key": "",
                "youtube_api_key": "",
                "http_retries": 0,
            }
        )
    return settings.model_copy(update=update)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass
class ReplayReport:
    mode: str
    scores: dict[str, float | None]
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(value == 1.0 for value in self.scores.values() if value is not None)

    @property
    def failure_class(self) -> str:
        for key, value in self.scores.items():
            if value is not None and value < 1.0:
                return key
        return ""

    def score_fields(self) -> dict[str, float]:
        return {key: value for key, value in self.scores.items() if value is not None}

    def summary(self) -> str:
        parts = []
        for key, _ in SCORES:
            tally = self.details.get(key.split("_", 1)[1], {})
            if self.scores.get(key) is not None and "of" in tally:
                parts.append(f"{key.split('_', 1)[1]} {tally['ok']}/{tally['of']}")
        return f"{'Live' if self.mode == 'live' else 'Replay'}: " + ", ".join(parts) + "."


def _ratio(ok: int, of: int) -> float | None:
    return round(ok / of, 3) if of else None


def _truth_keys(names: list[str], truth: list[dict[str, Any]]) -> set[str]:
    wanted = {_norm_name(name) for name in names if name} - {""}
    return {row["key"] for row in truth if wanted & {_norm_name(name) for name in row["names"]}}


def _km(a: GeoPoint, lat: float, lng: float) -> float:
    return haversine_km(a.lat, a.lng, lat, lng)


async def replay_case(case: EvalCase, settings: Settings) -> ReplayReport:
    """Score one japan case on its city's replayed (or, in live mode, live) provider data."""
    data = load_replay()
    spec = data["cases"].get(case.id) or {"city": case.destination or "Kyoto", "faults": [], "expect": []}
    city = data["cities"][spec["city"]]
    live = live_mode()
    replay = None if live else Replay(city["routes"], spec.get("faults"))
    faults = {} if live else {fault["route"]: fault for fault in spec.get("faults") or []}
    rsettings = replay_settings(settings, live)
    with use_transport(replay.transport() if replay is not None else None), _private_geocode_cache():
        parse, rows = await _parse_step(city, faults, rsettings, live)
        dedupe, conflicts = _merge_step(city, rows, live)
        gaps, plan_dupes = await _plan_step(case, city, spec, rsettings, live)
    if plan_dupes["of"]:
        dedupe["plan_duplicates"] = plan_dupes
    dedupe_score = _ratio(dedupe["ok"], dedupe["of"])
    if dedupe_score is not None and plan_dupes["duplicates"]:
        dedupe_score = round(min(dedupe_score, 1 - plan_dupes["duplicates"] / plan_dupes["of"]), 3)
    scores = {
        "replay_parse": _ratio(parse["ok"], parse["of"]),
        "replay_dedupe": dedupe_score,
        "replay_conflicts": _ratio(conflicts["ok"], conflicts["of"]),
        "replay_gaps": None if live else _ratio(gaps["ok"], gaps["of"]),
    }
    details = {
        "mode": "live" if live else "replay",
        "city": spec["city"],
        "note": spec.get("note", ""),
        "faults": sorted(faults),
        "parse": parse,
        "dedupe": dedupe,
        "conflicts": conflicts,
        "gaps": gaps,
    }
    if replay is not None and replay.misses:
        details["unrecorded_requests"] = sorted(set(replay.misses))[:10]
    return ReplayReport(mode=details["mode"], scores=scores, details=details)


async def _parse_step(city: dict[str, Any], faults: dict[str, dict], settings: Settings, live: bool):
    """Each provider parser on its route; returns the tally and the place rows by route."""
    name = city["city"]["name"]
    centre = GeoPoint(lat=city["city"]["lat"], lng=city["city"]["lng"])
    location = GeoLocation(name=name, country_code="JP", geo=centre, source="replay")
    start = date.today() + timedelta(days=2)
    end = start + timedelta(days=2)
    calls = {
        "google.geocode": lambda: GoogleGeocoder(settings).geocode(name),
        "openmeteo.geocode": lambda: OpenMeteoGeocoder(settings).geocode(name),
        "nominatim.geocode": lambda: NominatimGeocoder(settings).geocode(name),
        "google.attractions": lambda: GooglePlacesSource(settings).search(destination=name, geo=centre, category="attraction", limit=10),
        "google.restaurants": lambda: GooglePlacesSource(settings).search(destination=name, geo=centre, category="restaurant", limit=6),
        "nominatim.attractions": lambda: NominatimPlacesSource(settings).search(destination=name, geo=centre, category="attraction", limit=10),
        "nominatim.restaurants": lambda: NominatimPlacesSource(settings).search(destination=name, geo=centre, category="restaurant", limit=6),
        "wikivoyage": lambda: WikivoyagePlacesSource(settings).search(destination=name, geo=centre, category="attraction", limit=10),
        "openmeteo.forecast": lambda: OpenMeteoWeather(settings).forecast(location, start, end),
        "openmeteo.archive": lambda: OpenMeteoArchiveWeather(settings).forecast(location, start, end),
        "deepseek.search": lambda: DeepSeekSearchSource(settings).search(query=f"hotels in {name}", count=5, retries=0),
    }
    sources: list[dict[str, Any]] = []
    rows: dict[str, list] = {}
    for route_id, call in calls.items():
        try:
            result: Any = await call()
            error = None
        except ProviderError as exc:
            result, error = None, exc
        fault = faults.get(route_id) or {}
        ok, note = _parse_verdict(route_id, result, error, fault, city, centre, live, start, end)
        if ok is None:
            continue  # live: the source is not configured here
        if isinstance(result, list) and route_id in PLACE_ROUTES:
            rows[route_id] = result
        sources.append({"source": route_id, "ok": ok, "note": note})
    return {"ok": sum(row["ok"] for row in sources), "of": len(sources), "sources": sources}, rows


def _parse_verdict(route_id, result, error, fault, city, centre, live, start, end) -> tuple[bool | None, str]:
    if error is not None:
        if live:
            # A key that is not configured is not a parse result; anything else failed to fetch.
            if "not configured" in str(error).lower() or "no deepseek" in str(error).lower():
                return None, ""
            return False, f"{error.kind.value}: {str(error)[:120]}"
        # A geocoder whose body is a failing status (Google's OVER_QUERY_LIMIT, sent with HTTP 200) is a failed source too.
        expected = int(fault.get("status", 200)) >= 400 or "text" in fault or (
            route_id.endswith(".geocode") and fault.get("json") is not None
        )
        return expected, f"classified as {error.kind.value}" + ("" if expected else f" without a fault: {str(error)[:120]}")
    if route_id.endswith(".geocode"):
        if fault.get("json") is not None:
            return result is None, "status not OK: no result" if result is None else "returned a result for a failed status"
        if result is None:
            return False, "no result"
        km = _km(result.geo, centre.lat, centre.lng)
        return km <= CITY_KM, f"{result.name} at {km:.1f} km from the centre"
    if route_id == "openmeteo.forecast":
        days = (end - start).days + 1
        dropped = set(fault.get("drop_days") or [])
        ok_days = [row for row in result if row.status == DataStatus.OK]
        nulls = (fault.get("null") or {}).get("temperature_2m_max") or []
        invented = [index for index in nulls if index < len(result) and result[index].high_c is not None]
        if live:
            return len(result) == days and bool(ok_days), f"{len(ok_days)}/{days} forecast days"
        good = len(result) == days and len(ok_days) == days - len(dropped & set(range(days))) and not invented
        return good, f"{len(ok_days)}/{days} forecast days, {len(dropped)} left out by the source"
    if route_id == "openmeteo.archive":
        days = (end - start).days + 1
        stale = [row for row in result if row.status == DataStatus.STALE]
        return len(stale) == days, f"{len(stale)}/{days} seasonal reference days"
    rows = list(result or [])
    if live:
        return bool(rows), f"{len(rows)} rows"
    expected = city["parse"].get(route_id)
    named = all(getattr(row, "name", "") or getattr(row, "title", "") for row in rows)
    located = route_id == "wikivoyage" or route_id == "deepseek.search" or all(row.geo is not None for row in rows)
    return len(rows) == expected and named and located, f"{len(rows)} rows kept (expected {expected})"


def _merge_step(city: dict[str, Any], rows: dict[str, list], live: bool):
    truth = city["truth"]
    tolerance = 1.0 if live else 0.5
    inputs = [row.model_copy(deep=True) for route in PLACE_ROUTES for row in rows.get(route, [])]
    points: dict[str, list[GeoPoint]] = {}
    for row in inputs:
        for key in _truth_keys([row.name, *row.aliases], truth):
            if row.geo is not None:
                points.setdefault(key, []).append(row.geo)
            else:
                points.setdefault(key, [])
    merged = _merge_places(inputs)
    by_key: dict[str, list] = {}
    for record in merged:
        keys = _truth_keys([record.name, *record.aliases], truth)
        for key in keys:
            by_key.setdefault(key, []).append((record, keys))
    places = []
    ok = 0
    for key in sorted(points):
        hits = by_key.get(key, [])
        single = len(hits) == 1 and hits[0][1] == {key}
        ok += single
        places.append(
            {
                "place": key,
                "records": len(hits),
                "names": sorted({name for record, _ in hits for name in [record.name, *record.aliases]})[:6],
                "ok": single,
                **({"merged_with": sorted(hits[0][1] - {key})} if hits and hits[0][1] != {key} else {}),
            }
        )
    dedupe = {"ok": ok, "of": len(points), "inputs": len(inputs), "records": len(merged), "places": places}

    rows_out = []
    truth_by_key = {row["key"]: row for row in truth}
    for key, observed in sorted(points.items()):
        if len(observed) < 3:
            continue
        spread = max(haversine_km(a.lat, a.lng, b.lat, b.lng) for a in observed for b in observed)
        if spread <= 1.0:
            continue
        hits = by_key.get(key, [])
        row = truth_by_key[key]
        km = _km(hits[0][0].geo, row["lat"], row["lng"]) if len(hits) == 1 and hits[0][0].geo else None
        rows_out.append(
            {"place": key, "sources": len(observed), "spread_km": round(spread, 1),
             "chosen_km_from_truth": None if km is None else round(km, 2), "ok": km is not None and km <= tolerance}
        )
    conflicts = {"ok": sum(row["ok"] for row in rows_out), "of": len(rows_out), "places": rows_out}
    return dedupe, conflicts


async def _plan_step(case: EvalCase, city: dict[str, Any], spec: dict[str, Any], settings: Settings, live: bool):
    """The real plan path on the replayed (or live) registry, and what it recorded."""
    registry = ProviderRegistry.build(settings)
    registry.notes = []
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    plan_path = PlanPath(planner=Planner(gateway, settings, registry.fx), registry=registry, settings=settings)
    start = date.today() + timedelta(days=2)
    days = max(1, min(case.days or 3, 5))
    context = TripContext(
        session_id="replay",
        destination=city["city"]["name"],
        origin=case.origin or "Hong Kong",
        start_date=start,
        end_date=start + timedelta(days=days - 1),
        days=days,
        party_size=case.party_size or 2,
        budget=case.budget or 300000,
        currency=case.currency or "JPY",
        hard_constraints=list(case.constraints),
    )
    plan, error = None, ""
    try:
        plan = await plan_path.run(
            context=context,
            session_id="replay",
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id=f"replay-{case.id}"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
            version=1,
        )
    except Exception as exc:  # the score says the pipeline did not finish
        error = f"{exc.__class__.__name__}: {str(exc)[:160]}"
    expectations = [] if live else list(spec.get("expect") or [])
    checks = [_expectation(expectation, plan, city) for expectation in expectations]
    gaps = {
        "ok": sum(check["ok"] for check in checks),
        "of": len(checks),
        "checks": checks,
        "recorded": [f"{gap.section}: {gap.message[:120]}" for gap in plan.data_quality.gaps][:8] if plan else [],
        **({"error": error} if error else {}),
    }
    counts: Counter[str] = Counter()
    stops = 0
    for day in plan.days if plan else []:
        for item in day.items:
            if item.type.value != "attraction":
                continue
            stops += 1
            for key in _truth_keys([item.name], city["truth"]):
                counts[key] += 1
    duplicates = sum(count - 1 for count in counts.values() if count > 1)
    plan_dupes = {"of": stops, "duplicates": duplicates, "repeated": sorted(key for key, count in counts.items() if count > 1)}
    return gaps, plan_dupes


def _expectation(expectation: dict[str, Any], plan: Any, city: dict[str, Any]) -> dict[str, Any]:
    kind, want = next(iter(expectation.items()))
    label = f"{kind}: {json.dumps(want, ensure_ascii=False)}"
    if plan is None:
        return {"check": label, "ok": False, "found": "no plan"}
    if kind == "recorded":
        # A recovered failure is a data note, an unrecovered one a gap: either is on the record.
        record = [f"{gap.section}: {gap.message}" for gap in plan.data_quality.gaps] + list(plan.data_quality.notes)
        found = next((line for line in record if any(text.lower() in line.lower() for text in want["any"])), "")
        return {"check": label, "ok": bool(found), "found": found[:160] or "; ".join(record)[:200] or "nothing recorded"}
    if kind == "gap":
        for gap in plan.data_quality.gaps:
            if not gap.section.startswith(want["section"].rstrip("s")):
                continue
            if "contains" in want and want["contains"].lower() not in gap.message.lower():
                continue
            if "retryable" in want and gap.retryable != want["retryable"]:
                continue
            return {"check": label, "ok": True, "found": f"{gap.section}: {gap.message[:120]}"}
        return {"check": label, "ok": False, "found": "; ".join(f"{g.section}: {g.message[:60]}" for g in plan.data_quality.gaps) or "no gaps"}
    if kind == "weather_day":
        index = int(want["day"]) - 1
        weather = plan.days[index].weather if index < len(plan.days) else None
        status = weather.status.value if weather is not None else "missing"
        if "not_status" in want:
            ok = status != want["not_status"]
        else:  # a missing maximum must not come back as a forecast figure
            ok = weather is None or weather.status != DataStatus.OK or weather.high_c is None
        found = f"day {index + 1}: {status}, high {getattr(weather, 'high_c', None)}"
        return {"check": label, "ok": ok, "found": found}
    if kind == "weather_all":
        statuses = [day.weather.status.value if day.weather is not None else "missing" for day in plan.days]
        return {"check": label, "ok": bool(statuses) and all(s == want["status"] for s in statuses), "found": ", ".join(statuses)}
    if kind == "destination_within_km":
        geo = plan.destination.geo
        km = _km(geo, city["city"]["lat"], city["city"]["lng"]) if geo else None
        return {"check": label, "ok": km is not None and km <= float(want), "found": f"{plan.destination.name} at {km} km"}
    return {"check": label, "ok": False, "found": "unknown expectation"}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def replay_metrics(results: list[EvalCaseResult]) -> list[EvalMetric]:
    """Per-run means of the four TC-02 replay scores, for the trial report."""
    prefix = "Live" if live_mode() else "Replay"
    metrics: list[EvalMetric] = []
    for key, label in SCORES:
        values = [result.scores[key] for result in results if key in result.scores]
        if values:
            metrics.append(
                EvalMetric(
                    key=f"{key}_rate",
                    label=label.replace("Replay", prefix),
                    value=round(100.0 * sum(values) / len(values), 1),
                )
            )
    return metrics
