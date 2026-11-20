"""Weather, public holidays and exchange rates around a trip's dates.

Free sources only, all fine for commercial use with attribution:
- OpenStreetMap Nominatim: base city -> coordinates and country (1 request a second, cached forever)
- MET Norway Locationforecast: the ~9-day forecast (identifying User-Agent, cached for an hour)
- NASA POWER climatology (seasonality.py): "typical for <month>" beyond the forecast window
- Nager.Date: public holidays per country and year (cached 30 days)
- Frankfurter: European Central Bank reference rates (cached 6 hours)

Each source fails on its own: a missing piece is simply left out, never an error.
Failed calls are never cached, so they are retried on the next request.
"""

import asyncio
import json
import logging
import sqlite3
import time
from calendar import month_name
from collections import defaultdict
from datetime import date, datetime, timedelta

import httpx

from app.core.config import settings
from app.schemas.trip_document import DocEnrichment, DocExchange, DocHoliday, DocNote, DocPlace, DocSource, DocWeather
from app.services.place_media import CACHE_DB_PATH, HEADERS, _km
from app.services.seasonality import INDOOR, MONTHS, OUTDOOR, _climate, _names, season_tips

logger = logging.getLogger(__name__)

NOMINATIM = "https://nominatim.openstreetmap.org/search"
MET = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
NAGER = "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"
FRANKFURTER = "https://api.frankfurter.dev/v1/latest"
HOME_CURRENCY = "INR"  # when the brief has no home_currency (trips made before it existed)
FORECAST_DAYS = 8  # MET Norway reaches ~9-10 days ahead; the last day is usually partial
MAX_BASES = 8
NOMINATIM_INTERVAL = 1.1  # seconds between uncached lookups (Nominatim's usage policy: max 1/s)
MAX_POINTS, PLACE_RADIUS_KM, POINTS_BUDGET_SECONDS = 60, 60, 40.0  # map exports (locate_places)
TTL = {"geo_miss": 7 * 86400, "forecast": 3600, "holidays": 30 * 86400, "exchange": 6 * 3600}
FOREVER = 1e18

SOURCES = {
    "geo": DocSource(name="© OpenStreetMap contributors", url="https://www.openstreetmap.org/copyright", covers="Place lookup"),
    "forecast": DocSource(name="MET Norway (CC BY 4.0)", url="https://api.met.no/", covers="Weather forecast"),
    "typical": DocSource(name="NASA POWER climatology", url="https://power.larc.nasa.gov/", covers="Typical weather"),
    "holidays": DocSource(name="Nager.Date", url="https://date.nager.at/", covers="Public holidays"),
    "exchange": DocSource(name="Frankfurter (ECB reference rates)", url="https://frankfurter.dev/", covers="Exchange rates"),
}

# Countries whose currency Frankfurter quotes (the ECB set). Anything else just gets no local rate.
_EURO = "ad at be bg cy de ee es fi fr gr hr ie it lt lu lv mc me mt nl pt si sk sm va"
CURRENCY_OF = {**dict.fromkeys(_EURO.split(), "EUR"),
               "au": "AUD", "br": "BRL", "ca": "CAD", "ch": "CHF", "li": "CHF", "cn": "CNY", "cz": "CZK", "dk": "DKK",
               "gb": "GBP", "hk": "HKD", "hu": "HUF", "id": "IDR", "il": "ILS", "in": "INR", "is": "ISK", "jp": "JPY",
               "kr": "KRW", "mx": "MXN", "my": "MYR", "no": "NOK", "nz": "NZD", "ph": "PHP", "pl": "PLN", "ro": "RON",
               "se": "SEK", "sg": "SGD", "th": "THB", "tr": "TRY", "us": "USD", "za": "ZAR"}

SEVERITY = ["sun", "partly", "cloud", "fog", "rain", "snow", "storm"]
Places = list[tuple[str, str]]  # (name, text to match against the outdoor/indoor rules)
DayInput = tuple[int, str, Places]  # (day number, base city, planned places)

_db_lock = asyncio.Lock()
_nominatim_lock = asyncio.Lock()
_last_nominatim = [0.0]


# ---------- cache ----------

def _connect() -> sqlite3.Connection:
    CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(CACHE_DB_PATH, timeout=3)
    connection.execute("CREATE TABLE IF NOT EXISTS enrichment (key TEXT PRIMARY KEY, data TEXT, expires REAL NOT NULL)")
    return connection


def _cache_get(key: str) -> tuple[bool, object]:
    connection = _connect()
    try:
        row = connection.execute("SELECT data, expires FROM enrichment WHERE key = ?", (key,)).fetchone()
    finally:
        connection.close()
    if not row or row[1] < time.time():
        return False, None
    return True, json.loads(row[0]) if row[0] is not None else None


def _cache_put(key: str, data: object, ttl: float | None) -> None:
    connection = _connect()
    try:
        with connection:
            connection.execute("INSERT OR REPLACE INTO enrichment VALUES (?, ?, ?)",
                               (key, json.dumps(data) if data is not None else None, time.time() + ttl if ttl else FOREVER))
    finally:
        connection.close()


async def _cached(key: str, ttl: float | None, fetch, miss_ttl: float | None = None):
    """fetch() once per cache window. A None result is kept only when miss_ttl is set; errors never are."""
    async with _db_lock:
        hit, data = _cache_get(key)
    if hit:
        return data
    try:
        data = await fetch()
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        logger.info("Enrichment source unavailable (%s): %s", key, exc)
        return None
    if data is not None or miss_ttl:
        async with _db_lock:
            _cache_put(key, data, ttl if data is not None else miss_ttl)
    return data


# ---------- places ----------

async def _search(client: httpx.AsyncClient, query: str) -> dict | None:
    """Nominatim's best match for a free-text query, cached (hits forever, misses for a week)."""
    async def fetch():
        async with _nominatim_lock:
            await asyncio.sleep(max(0.0, _last_nominatim[0] + NOMINATIM_INTERVAL - time.monotonic()))
            _last_nominatim[0] = time.monotonic()
            response = await client.get(NOMINATIM, params={
                "q": query, "format": "jsonv2", "limit": 1, "addressdetails": 1, "accept-language": "en"})
        response.raise_for_status()
        hits = response.json()
        if not hits:
            return None
        country = ((hits[0].get("address") or {}).get("country_code") or "").lower()
        return {"lat": float(hits[0]["lat"]), "lon": float(hits[0]["lon"]), "country": country or None}
    return await _cached(f"geo|{query.casefold()}", None, fetch, miss_ttl=TTL["geo_miss"])


async def _locate(client: httpx.AsyncClient, base: str, destination: str | None) -> dict | None:
    """{"lat", "lon", "country"} for a base city, trying it with the destination as context first."""
    same = not destination or base.casefold() == destination.casefold()
    for query in [base] if same else [f"{base}, {destination}", base]:
        if found := await _search(client, query):
            return found
    return None


async def locate_places(destination: str | None, places: list[tuple[str, str, str]]) -> list[dict]:
    """Coordinates for (id, name, base) places, for map exports. Each is searched with its base as context
    and kept only within PLACE_RADIUS_KM of that base, so namesakes elsewhere are dropped. Uncached lookups
    run at Nominatim's 1/s, so a time budget returns what's found; a later call picks up the rest from cache."""
    deadline = time.monotonic() + POINTS_BUDGET_SECONDS
    found: list[dict] = []
    async with httpx.AsyncClient(timeout=10.0, headers=HEADERS) as client:
        bases: dict[str, dict | None] = {}
        for place_id, name, base in places[:MAX_POINTS]:
            if time.monotonic() > deadline:
                break
            if base not in bases:
                bases[base] = await _locate(client, base, destination)
            anchor = bases[base]
            if not anchor:
                continue  # without the base's position there's no way to reject a namesake
            contexts = [base] if not destination or base.casefold() == destination.casefold() else [base, destination]
            for context in contexts:
                spot = await _search(client, f"{name}, {context}")
                if spot and _km(anchor["lat"], anchor["lon"], spot["lat"], spot["lon"]) <= PLACE_RADIUS_KM:
                    found.append({"id": place_id, "lat": spot["lat"], "lon": spot["lon"]})
                    break
    return found


# ---------- weather ----------

def _condition(symbol: str) -> str:
    """MET Norway symbol code -> one of SEVERITY."""
    code = symbol.split("_")[0]
    if "thunder" in code:
        return "storm"
    if "snow" in code or "sleet" in code:
        return "snow"
    if "rain" in code:
        return "rain"
    if code == "fog":
        return "fog"
    if code == "cloudy":
        return "cloud"
    return "partly" if code == "partlycloudy" else "sun"  # clearsky, fair


def daily_forecast(timeseries: list[dict], lon: float) -> dict[date, dict]:
    """Per local date: temperature range and mean, total rain, and the worst daytime sky."""
    # ponytail: solar offset from longitude, not the real time zone; can shift a day boundary by an hour.
    offset = timedelta(hours=round(lon / 15))
    days = defaultdict(lambda: {"temps": [], "rain": 0.0, "sky": []})
    for step in timeseries:
        local = datetime.fromisoformat(step["time"].replace("Z", "+00:00")) + offset
        data, day = step["data"], days[local.date()]
        day["temps"].append(data["instant"]["details"]["air_temperature"])
        # Hourly steps carry next_1_hours; later 6-hourly steps only next_6_hours, so nothing is counted twice.
        period = data.get("next_1_hours") or data.get("next_6_hours") or {}
        day["rain"] += (period.get("details") or {}).get("precipitation_amount") or 0.0
        symbol = (period.get("summary") or {}).get("symbol_code")
        if symbol and 6 <= local.hour <= 18:
            day["sky"].append(_condition(symbol))
    return {when: {"temp_min": min(v["temps"]), "temp_max": max(v["temps"]),
                   "temp_mean": round(sum(v["temps"]) / len(v["temps"]), 1), "rain_mm": round(v["rain"], 1),
                   "condition": max(v["sky"], key=SEVERITY.index) if v["sky"] else None}
            for when, v in days.items() if len(v["temps"]) >= 3}  # drop the trailing single-step day


def forecast_tips(day: dict, places: Places) -> dict:
    """Badge and notes for one forecast day: the forecast counterpart of seasonality.season_tips."""
    outdoor = [name for name, text in places if OUTDOOR.search(text)]
    indoor = [name for name, text in places if INDOOR.search(text)]
    rain, notes, badge = day["rain_mm"], [], None
    backup = (f" Have an indoor backup for {_names(outdoor)}" + (f", such as {_names(indoor)}." if indoor else ".")) if outdoor else ""
    if day["condition"] == "storm":
        badge = {"tone": "warn", "text": "Storms"}
        notes.append({"tone": "warn", "text": "Thunderstorms forecast." + backup})
    elif rain >= 5:
        badge = {"tone": "warn", "text": "Rain"}
        notes.append({"tone": "warn", "text": f"Rain forecast, about {rain:.0f} mm. Pack an umbrella." + backup})
    elif rain >= 1:
        badge = {"tone": "info", "text": "Showers"}
        notes.append({"tone": "info", "text": "Showers possible: pack an umbrella."})
    if day["temp_max"] >= 32:
        badge = badge or {"tone": "warn", "text": "Hot"}
        notes.append({"tone": "warn", "text": f"Hot, up to {day['temp_max']:.0f} °C."
                      + (f" Do {_names(outdoor)} early or late in the day." if outdoor else " Carry water.")})
    elif day["temp_min"] <= 0:
        badge = badge or {"tone": "warn", "text": "Freezing"}
        notes.append({"tone": "warn", "text": f"Freezing, down to {day['temp_min']:.0f} °C. Pack warm layers."})
    return {"badge": badge or {"tone": "good", "text": "Dry"}, "notes": notes}


async def _weather(client: httpx.AsyncClient, start: date, days: list[DayInput], spots: dict, used: set) -> list[DocWeather]:
    today = date.today()
    dated = [(number, start + timedelta(days=number - 1), base, places) for number, base, places in days if spots.get(base)]

    async def forecast(base: str):
        spot = spots[base]
        async def fetch():
            response = await client.get(MET, params={"lat": f"{spot['lat']:.2f}", "lon": f"{spot['lon']:.2f}"})
            response.raise_for_status()
            return response.json()["properties"]["timeseries"]
        series = await _cached(f"forecast|{spot['lat']:.2f},{spot['lon']:.2f}", TTL["forecast"], fetch)
        try:
            return base, daily_forecast(series, spot["lon"]) if series else {}
        except (KeyError, TypeError, ValueError) as exc:
            logger.info("Unreadable forecast for %s: %s", base, exc)
            return base, {}

    async def climate(base: str):
        return base, await _climate(client, f"{spots[base]['lat']:.1f},{spots[base]['lon']:.1f}")

    soon = {base for _, when, base, _ in dated if today <= when <= today + timedelta(days=FORECAST_DAYS)}
    forecasts = dict(await asyncio.gather(*(forecast(base) for base in soon)))
    later = {base for _, when, base, _ in dated if when not in forecasts.get(base, {})}
    climates = dict(await asyncio.gather(*(climate(base) for base in later)))

    weather = []
    for number, when, base, places in dated:
        if (day := forecasts.get(base, {}).get(when)) is not None:
            used.add("forecast")
            tips = forecast_tips(day, places)
            weather.append(DocWeather(day=number, date=when, base=base, kind="forecast", label="Forecast", **day,
                                      badge=DocNote(**tips["badge"]), notes=[DocNote(**n) for n in tips["notes"]]))
        elif climates.get(base):
            used.add("typical")
            temp_c, rain = climates[base][MONTHS[when.month - 1]]
            tips = season_tips(when.month, temp_c, rain, places)
            weather.append(DocWeather(day=number, date=when, base=base, kind="typical",
                                      label=f"Typical for {month_name[when.month]}", temp_mean=round(temp_c, 1),
                                      rain_mm=round(rain, 1), badge=DocNote(**tips["badge"]),
                                      notes=[DocNote(**n) for n in tips["notes"]]))
    return weather


# ---------- holidays and money ----------

async def _holidays(client: httpx.AsyncClient, start: date, days: list[DayInput], spots: dict, used: set) -> list[DocHoliday]:
    country_of = {base: spot["country"] for base, spot in spots.items() if spot and spot["country"]}
    end = start + timedelta(days=max(number for number, _, _ in days) - 1)
    wanted = {(country, year) for country in set(country_of.values()) for year in range(start.year, end.year + 1)}

    async def fetch_year(country: str, year: int):
        async def fetch():
            response = await client.get(NAGER.format(year=year, country=country.upper()))
            if response.status_code in (204, 404):
                return []  # a country Nager.Date doesn't cover
            response.raise_for_status()
            return response.json()
        return country, await _cached(f"holidays|{country}|{year}", TTL["holidays"], fetch)

    by_country = defaultdict(list)
    for country, entries in await asyncio.gather(*(fetch_year(c, y) for c, y in sorted(wanted))):
        if entries is not None:  # None = the lookup failed; the credit only shows for data we have
            by_country[country] += entries
    found, seen = [], set()
    for number, base, _ in days:
        when, country = start + timedelta(days=number - 1), country_of.get(base)
        for entry in by_country.get(country, []):
            key = (when, entry.get("name"))
            if entry.get("date") != when.isoformat() or "Public" not in (entry.get("types") or ["Public"]) or key in seen:
                continue
            seen.add(key)
            found.append(DocHoliday(day=number, date=when, name=entry["name"], local_name=entry.get("localName"),
                                    country=country.upper(), regional=bool(entry.get("counties"))))
    if by_country:
        used.add("holidays")
    return found


async def _exchange(client: httpx.AsyncClient, currency: str, local: set, home: str, used: set) -> DocExchange | None:
    symbols = sorted(code for code in local | {home} if code and code != currency)
    if not symbols:
        return None
    async def fetch():
        response = await client.get(FRANKFURTER, params={"base": currency, "symbols": ",".join(symbols)})
        response.raise_for_status()
        body = response.json()
        return {"rates": body["rates"], "date": body["date"]}
    data = await _cached(f"exchange|{currency}|{','.join(symbols)}", TTL["exchange"], fetch)
    if not data or not data["rates"]:
        return None
    used.add("exchange")
    return DocExchange(base=currency, rates=data["rates"], date=data["date"])


# ---------- entry points ----------

async def enrich(client: httpx.AsyncClient, destination: str | None, start: date | None,
                 days: list[DayInput], currency: str, home: str | None = None) -> DocEnrichment:
    bases = list(dict.fromkeys(base for _, base, _ in days if base))[:MAX_BASES]
    spots = dict(zip(bases, await asyncio.gather(*(_locate(client, base, destination) for base in bases))))
    used = {"geo"} if any(spots.values()) else set()
    local = {CURRENCY_OF.get(spot["country"]) for spot in spots.values() if spot}
    weather, holidays, exchange = await asyncio.gather(
        _weather(client, start, days, spots, used) if start else asyncio.sleep(0, []),
        _holidays(client, start, days, spots, used) if start and days else asyncio.sleep(0, []),
        _exchange(client, currency, local, home or HOME_CURRENCY, used),
    )
    return DocEnrichment(places=[DocPlace(name=base, **spot) for base, spot in spots.items() if spot],
                         weather=weather, holidays=holidays, exchange=exchange,
                         sources=[source for key, source in SOURCES.items() if key in used])


async def get_enrichment(destination: str | None, start: date | None, days: list[DayInput], currency: str,
                         timeout: float = 15.0, home: str | None = None) -> DocEnrichment | None:
    """Conditions for a trip, or None when switched off, there's nothing to look up, or everything failed."""
    if not settings.ENRICHMENT_ENABLED or not days:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=HEADERS) as client:
            return await asyncio.wait_for(enrich(client, destination, start, days, currency, home), timeout)
    except Exception as exc:  # never let enrichment break the document or a planning turn
        logger.warning("Trip enrichment failed: %s", exc)
        return None


async def draft_conditions(destination: str | None, start: date | None, days: int | None, currency: str) -> dict | None:
    """Forecast days and public holidays for a one-shot draft, which has no day plan yet (so every day is
    looked up at the destination). Typical weather is left to seasonality.trip_season's "season" block;
    only real forecasts appear here. None when there's nothing worth telling the planner."""
    if not destination or not start or not days:
        return None
    enrichment = await get_enrichment(destination, start, [(n, destination, []) for n in range(1, days + 1)],
                                      currency, timeout=8.0)
    if not enrichment:
        return None
    day_name = lambda when: when.strftime("%a %d %b")  # noqa: E731
    forecast = [{"day": w.day, "date": day_name(w.date), "sky": w.condition, "rain_mm": w.rain_mm,
                 "temp_c": f"{w.temp_min:.0f}-{w.temp_max:.0f}", **({"note": w.notes[0].text} if w.notes else {})}
                for w in enrichment.weather if w.kind == "forecast"]
    holidays = [{"day": h.day, "date": day_name(h.date), "name": h.name, **({"regional": True} if h.regional else {})}
                for h in enrichment.holidays]
    if not forecast and not holidays:
        return None
    return {**({"forecast": forecast} if forecast else {}), **({"public_holidays": holidays} if holidays else {})}


def agent_facts(enrichment: DocEnrichment | None, day: int) -> dict | None:
    """Compact conditions for the planner's FACTS: the current day in detail, the rest as heads-ups."""
    if not enrichment or not (enrichment.weather or enrichment.holidays):
        return None
    weather = next((w for w in enrichment.weather if w.day == day), None)
    heads_up = [f"Day {h.day}: {h.name} (public holiday{', some regions only' if h.regional else ''})"
                for h in enrichment.holidays if h.day != day]
    heads_up += [f"Day {w.day}: {w.badge.text} ({'forecast' if w.kind == 'forecast' else w.label})" for w in enrichment.weather
                 if w.day != day and w.badge and w.badge.tone == "warn"]
    return {
        "current_day": {
            "weather": {"label": weather.label, "condition": weather.condition, "temp_min": weather.temp_min,
                        "temp_max": weather.temp_max, "temp_mean": weather.temp_mean, "rain_mm": weather.rain_mm,
                        "notes": [n.text for n in weather.notes]} if weather else None,
            "holidays": [h.name + (" (some regions only)" if h.regional else "") for h in enrichment.holidays if h.day == day],
        },
        "heads_up": heads_up[:6],
    }
