"""Hong Kong official status sources: HKO weather warnings and MTR line status.

Both are keyless government open data. Each reports what is in force *now*, so a
source answers only for today's date in Hong Kong; for a trip that does not
include today it returns nothing without a network request.

- Hong Kong Observatory, Weather Warning Summary (``dataType=warnsum``): one entry
  per warning type, e.g. ``{"WTCSGNL": {"code": "TC8NE", "actionCode": "ISSUE",
  "issueTime": ..., "expireTime": ...}}``. An empty object means no warning.
- MTR Next Train (data.gov.hk): ``getSchedule.php?line=TWL&sta=CEN`` returns
  ``isdelay`` ("Y"/"N") and ``status`` (1 normal, 0 alert or error) per line.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.config import Settings
from app.domain.errors import ProviderError
from app.domain.models import FailureKind, TransitStatus, WeatherAlert, utcnow
from app.infrastructure.http import request_json

HONG_KONG_TZ = timezone(timedelta(hours=8))  # no daylight saving

HKO_WARNSUM_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
HKO_WARNINGS_PAGE = "https://www.hko.gov.hk/en/wxinfo/dailywx/wxwarntoday.htm"
MTR_SCHEDULE_URL = "https://rt.data.gov.hk/v1/transport/mtr/getSchedule.php"
MTR_STATUS_PAGE = "https://www.mtr.com.hk/en/customer/services/train_service_index.html"

# HKO warning codes -> (name, severe). Severe moves outdoor stops off the day.
HKO_WARNINGS: dict[str, tuple[str, bool]] = {
    "TC1": ("Standby Signal No. 1", False),
    "TC3": ("Strong Wind Signal No. 3", True),
    "TC8NE": ("Typhoon Signal No. 8 (Northeast Gale or Storm)", True),
    "TC8SE": ("Typhoon Signal No. 8 (Southeast Gale or Storm)", True),
    "TC8NW": ("Typhoon Signal No. 8 (Northwest Gale or Storm)", True),
    "TC8SW": ("Typhoon Signal No. 8 (Southwest Gale or Storm)", True),
    "TC9": ("Typhoon Signal No. 9 (Increasing Gale or Storm)", True),
    "TC10": ("Typhoon Signal No. 10 (Hurricane)", True),
    "WRAINA": ("Amber Rainstorm Warning Signal", False),
    "WRAINR": ("Red Rainstorm Warning Signal", True),
    "WRAINB": ("Black Rainstorm Warning Signal", True),
    "WTMW": ("Tsunami Warning", True),
}

# MTR line code -> (line name, the station the status is read from).
MTR_LINES: dict[str, tuple[str, str]] = {
    "AEL": ("Airport Express", "HOK"),
    "TCL": ("Tung Chung Line", "HOK"),
    "TML": ("Tuen Ma Line", "HUH"),
    "TKL": ("Tseung Kwan O Line", "TKO"),
    "EAL": ("East Rail Line", "HUH"),
    "SIL": ("South Island Line", "ADM"),
    "TWL": ("Tsuen Wan Line", "CEN"),
    "ISL": ("Island Line", "ADM"),
    "KTL": ("Kwun Tong Line", "MOK"),
    "DRL": ("Disneyland Resort Line", "DIS"),
}


def hong_kong_today() -> date:
    return datetime.now(HONG_KONG_TZ).date()


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_warnsum(data: dict[str, Any], *, today: date, start: date, end: date, source: str) -> list[WeatherAlert]:
    """The warnings in force today (and until each one's expiry), within the trip."""
    alerts: list[WeatherAlert] = []
    for statement, entry in sorted(data.items()):
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("code") or statement)
        if code == "CANCEL" or str(entry.get("actionCode") or "").upper() == "CANCEL":
            continue
        name, severe = HKO_WARNINGS.get(code, (str(entry.get("name") or code), False))
        issued = _parse_time(entry.get("issueTime"))
        expires = _parse_time(entry.get("expireTime"))
        last = max(today, expires.astimezone(HONG_KONG_TZ).date()) if expires else today
        day = max(today, start)
        while day <= min(last, end):
            alerts.append(
                WeatherAlert(
                    date=day,
                    code=code,
                    name=name,
                    severe=severe,
                    source=source,
                    url=HKO_WARNINGS_PAGE,
                    issued_at=issued,
                    fetched_at=utcnow(),
                )
            )
            day += timedelta(days=1)
    return alerts


class HkoWarningsSource:
    """Hong Kong Observatory weather warnings (keyless open data)."""

    name = "hko-warnings"

    def __init__(self, settings: Settings):
        self.settings = settings

    def covers(self, *, start: date, end: date) -> bool:
        """A warning in force now says nothing about another day."""
        return start <= hong_kong_today() <= end

    async def alerts(self, *, start: date, end: date) -> list[WeatherAlert]:
        today = hong_kong_today()
        if not start <= today <= end:
            return []
        data = await request_json(
            "GET",
            HKO_WARNSUM_URL,
            source=self.name,
            params={"dataType": "warnsum", "lang": "en"},
            timeout=min(self.settings.http_timeout_seconds, 10.0),
            retries=1,
            user_agent=self.settings.user_agent,
        )
        return parse_warnsum(data, today=today, start=start, end=end, source=self.name)


def parse_line_status(data: dict[str, Any], *, line: str, today: date, source: str) -> TransitStatus:
    """One line's status from a Next Train response; an error response raises."""
    name = MTR_LINES.get(line, (line, ""))[0]
    if isinstance(data.get("error"), dict) or data.get("resultCode") == 0:
        error = data.get("error") or {}
        raise ProviderError(
            f"{source}: {line} {error.get('errorCode', '')} {error.get('errorMsg') or data.get('message', '')}".strip(),
            kind=FailureKind.NO_DATA,
            source=source,
            retryable=False,
        )
    delayed = str(data.get("isdelay") or "N").upper() == "Y"
    status = data.get("status")
    message = str(data.get("message") or "")
    # status 0 without an error is a special train service arrangement.
    alert = str(status) == "0"
    if delayed or alert:
        text = f"MTR reports {'a delay' if delayed else 'special service arrangements'} on the {name}."
        if alert and message and message.lower() != "successful":
            text += f" {message}"
    else:
        text = f"{name}: normal service."
    return TransitStatus(
        line=line,
        line_name=name,
        date=today,
        delayed=delayed or alert,
        message=text[:300],
        source=source,
        url=str(data.get("url") or MTR_STATUS_PAGE),
        fetched_at=utcnow(),
    )


class MtrLineStatusSource:
    """MTR Next Train service status per line (keyless, data.gov.hk)."""

    name = "mtr-next-train"

    def __init__(self, settings: Settings):
        self.settings = settings

    def covers(self, *, start: date, end: date) -> bool:
        """The feed reports the network as it is now."""
        return start <= hong_kong_today() <= end

    async def status(self, *, lines: list[str], start: date, end: date) -> list[TransitStatus]:
        today = hong_kong_today()
        wanted = [line for line in dict.fromkeys(lines) if line in MTR_LINES]
        if not wanted or not start <= today <= end:
            return []
        results = await asyncio.gather(*(self._line(line, today) for line in wanted), return_exceptions=True)
        statuses = [row for row in results if isinstance(row, TransitStatus)]
        if not statuses:
            first = next((row for row in results if isinstance(row, Exception)), None)
            if isinstance(first, ProviderError):
                raise first
            raise ProviderError(f"{self.name} returned no line status", kind=FailureKind.NO_DATA, source=self.name)
        return statuses

    async def _line(self, line: str, today: date) -> TransitStatus:
        data = await request_json(
            "GET",
            MTR_SCHEDULE_URL,
            source=self.name,
            params={"line": line, "sta": MTR_LINES[line][1], "lang": "EN"},
            timeout=min(self.settings.http_timeout_seconds, 8.0),
            retries=0,
            user_agent=self.settings.user_agent,
        )
        return parse_line_status(data, line=line, today=today, source=self.name)
