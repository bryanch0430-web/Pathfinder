"""Turn a message into trip slots.

Two channels: deterministic extraction (always available, auditable) and the
writer model (fills what the rules cannot). The model may leave a slot null; it
may never overwrite a slot the traveller did not mention with a guess that
contradicts an existing value.
"""

from __future__ import annotations

import re
import calendar
from datetime import date, timedelta

from app.application.llm_gateway import LLMGateway
from app.domain.errors import CanaryLeak
from app.domain.models import TripContext

# Interest and style tags the rules recognise. Any other tag is read by the
# planner as a place the traveller wants to visit.
PREFERENCE_PATTERNS = {
    "vegetarian": r"\bvegetarian\b",
    "vegan": r"\bvegan\b",
    "halal": r"\bhalal\b",
    "no spicy food": r"\b(no spicy|not spicy|mild food)\b",
    "slow pace": r"\b(slow pace|relaxed|not rushed|easy pace)\b",
    "fast pace": r"\b(fast pace|packed|see as much as possible)\b",
    "kid friendly": r"\b(kid|child|family)[- ]friendly\b|\bwith (kids|children)\b",
    "museums": r"\bmuseums?\b",
    "nature": r"\b(nature|hiking|outdoors?)\b",
    "shopping": r"\bshopping\b",
    "food": r"\bfood|restaurant|eat\b",
}

CITY_HINTS = [
    "Hong Kong",
    "Kyoto",
    "Tokyo",
    "Osaka",
    "Nara",
    "Sapporo",
    "Fukuoka",
    "Taipei",
    "Singapore",
    "Bangkok",
    "Chiang Mai",
    "Seoul",
    "Busan",
    "Shanghai",
    "Beijing",
    "Guangzhou",
    "Shenzhen",
    "London",
    "Paris",
    "Rome",
    "Milan",
    "Barcelona",
    "Madrid",
    "Amsterdam",
    "Berlin",
    "Prague",
    "Vienna",
    "Istanbul",
    "New York",
    "Los Angeles",
    "San Francisco",
    "Vancouver",
    "Toronto",
    "Sydney",
    "Melbourne",
    "Auckland",
    "Dubai",
    "Bali",
    "Hanoi",
    "Ho Chi Minh City",
    "Manila",
    "Kuala Lumpur",
    "Maldives",
    "Reykjavik",
]

CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "HK$": "HKD", "S$": "SGD", "฿": "THB"}

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


async def understand(
    message: str,
    context: TripContext,
    gateway: LLMGateway,
    *,
    today: date | None = None,
) -> TripContext:
    today = today or date.today()
    updated = context.model_copy(deep=True)
    before = updated.model_dump()
    labeled = _labeled_clarify(message)
    if labeled:
        # A clarify form answers with "slot: value" pairs; parse those
        # deterministically instead of re-reading the sentence as prose (which
        # used to turn "in three months" into a destination).
        _apply_labeled(updated, labeled, today)
    else:
        _apply_rules(message, updated, today)

    # The writer is only asked to fill slots the rules could not fill. On a
    # complete context it is skipped entirely: a hotel swap must never silently
    # rewrite the trip dates, and the common turn stays fast.
    missing = updated.missing_slots()
    if gateway.available and missing:
        prompt = (
            f"Current date: {today.isoformat()}\n"
            f"Existing context: {updated.model_dump_json()}\n"
            f"Still missing: {missing}\n\n"
            f"Traveller message:\n<user_travel_request>\n{message}\n</user_travel_request>"
        )
        try:
            data = await gateway.json("understand", user=prompt, purpose="understand")
            _apply_model_patch(updated, data, allowed=_allowed_patch(before))
        except CanaryLeak:
            raise
        except Exception:
            # Deterministic extraction stands on its own.
            pass

    _normalise_dates(updated)
    return updated


def _apply_rules(message: str, context: TripContext, today: date) -> None:
    text = message.strip()
    lower = text.lower()

    # party size
    party = None
    patterns = [
        r"\b(\d{1,2})\s*(?:people|persons?|travellers?|travelers?|adults?|guests?|pax)\b",
        r"\b(?:party|group)\s+of\s+(\d{1,2})\b",
        r"\bfor\s+(\d{1,2})\s*(?:people|persons?|travellers?|travelers?)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, lower)
        if match:
            party = int(match.group(1))
            break
    if party is None:
        if re.search(r"\bsolo\b", lower):
            party = 1
        elif re.search(r"\bcouple\b", lower):
            party = 2
        else:
            family = re.search(r"\bfamily of (one|two|three|four|five|six|\d)\b", lower)
            if family:
                token = family.group(1)
                party = NUMBER_WORDS.get(token, int(token) if token.isdigit() else None)
    if party:
        context.party_size = party

    # duration
    days = None
    match = re.search(r"\b(\d{1,2})[\s-]*(?:day|night)s?\b", lower)
    if match:
        days = int(match.group(1))
        if "night" in match.group(0):
            days = max(days, 1)
    elif re.search(r"\b(?:a|one)\s+week\b", lower):
        days = 7
    elif re.search(r"\b(?:two|2)\s+weeks\b", lower):
        days = 14
    elif re.search(r"\bweekend\b", lower):
        days = 2
    if days:
        context.days = days

    # budget: currency symbol or code plus amount
    budget_match = re.search(
        r"(?:budget|under|below|max(?:imum)?|around|about|spend)?\s*"
        r"(?:(HK\$|S\$|[$€£¥฿])|(USD|EUR|GBP|JPY|HKD|CNY|SGD|THB|TWD|KRW|AUD|CAD))\s*"
        r"([0-9][0-9,]*(?:\.[0-9]+)?)",
        text,
        re.IGNORECASE,
    )
    if not budget_match:
        budget_match = re.search(
            r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(USD|EUR|GBP|JPY|HKD|CNY|SGD|THB|TWD|KRW|AUD|CAD)\b",
            text,
            re.IGNORECASE,
        )
        if budget_match:
            context.budget = float(budget_match.group(1).replace(",", ""))
            context.currency = budget_match.group(2).upper()
    if budget_match and "budget" not in (context.currency or "").lower():
        amount = budget_match.group(3) if budget_match.lastindex and budget_match.lastindex >= 3 else budget_match.group(1)
        symbol = budget_match.group(1) if budget_match.lastindex and budget_match.lastindex >= 3 else ""
        code = budget_match.group(2) if budget_match.lastindex and budget_match.lastindex >= 3 else budget_match.group(2)
        try:
            context.budget = float(str(amount).replace(",", ""))
        except (TypeError, ValueError):
            pass
        if code:
            context.currency = code.upper()
        elif symbol in CURRENCY_SYMBOLS:
            context.currency = CURRENCY_SYMBOLS[symbol]

    # destination / origin. The origin is resolved first: "a Kyoto trip from
    # Hong Kong" must not treat the departure city as the destination.
    previous_destination = context.destination
    origin = _find_origin(lower)
    if origin:
        context.origin = origin
    destination = _find_place(lower, ["to", "in", "visit", "explore", "around"], origin)
    if destination:
        context.destination = destination
    destination_changed = bool(destination and destination.strip().lower() != previous_destination.strip().lower())

    # dates: an existing, explicit range is authoritative. "in November" must
    # not move a trip the traveller already set in the form to 1 November.
    # A different destination is a different trip, so its dates come from this
    # message (or from the day count), never from the previous city's plan.
    old_start, old_end = context.start_date, context.end_date
    if destination_changed or not (old_start and old_end):
        start, end = _find_dates(lower, today)
        if start:
            context.start_date = start
            context.end_date = end
        elif destination_changed:
            context.start_date = old_start
            context.end_date = (
                old_start + timedelta(days=context.days - 1)
                if old_start and context.days
                else old_end
            )

    # style and preferences
    if re.search(r"\b(luxur|5[- ]star|five[- ]star)", lower):
        context.hotel_style = "luxury"
    elif re.search(r"\b(boutique|design hotel)", lower):
        context.hotel_style = "boutique"
    elif re.search(r"\b(hostel|dorm)", lower):
        context.hotel_style = "hostel"
    elif re.search(r"\b(budget|cheap|affordable)", lower):
        context.hotel_style = "budget"
    elif re.search(r"\b(midrange|mid-range|3[- ]star|comfortable)", lower):
        context.hotel_style = "midrange"

    for label, pattern in PREFERENCE_PATTERNS.items():
        if re.search(pattern, lower) and label not in context.preferences:
            context.preferences.append(label)

    # A named must-see ("I want Fushimi Inari") becomes a preference the planner
    # can honour by scheduling it before the crowds.
    for match in re.finditer(
        r"\b(?:i want|i'd like|we want|must see|must visit|don'?t miss|do not miss|include|specifically)\b"
        r"([^.,;!?]{0,60})",
        text,
        re.IGNORECASE,
    ):
        tail = re.sub(
            r"^\s*(?:to\s+)?(?:see|visit|go to|stay in)\s+",
            "",
            match.group(1),
            flags=re.IGNORECASE,
        )
        named = re.match(r"\s*(?:the\s+)?([A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*){0,3})", tail)
        if not named:
            continue
        preference = f"must-see: {named.group(1).strip()}"
        if preference not in context.preferences:
            context.preferences.append(preference)

    for constraint in _hard_constraints(text):
        if constraint not in context.hard_constraints:
            context.hard_constraints.append(constraint)

    if re.search(r"[\u4e00-\u9fff]", text):
        context.language = "zh-Hant" if re.search(r"[台灣台裏裡]", text) else "zh-Hans"
    else:
        context.language = context.language or "en"


def _find_place(lower: str, prepositions: list[str], origin: str | None = None) -> str | None:
    """Resolve the destination, never the departure city.

    Order matters: an explicit destination cue ("to Kyoto", "Kyoto trip") beats a
    bare mention anywhere else in the sentence, and the origin is excluded at
    every step. Otherwise "a Kyoto trip from Hong Kong" resolves to Hong Kong,
    because a bare city scan finds the origin first.
    """
    origin_lower = (origin or "").strip().lower()
    cities = [(city, city.lower()) for city in CITY_HINTS]

    # 1. Explicit destination cue: "<to|in|visit|explore|around> <city>".
    for prep in prepositions:
        for city, needle in cities:
            if needle == origin_lower:
                continue
            if re.search(rf"\b{prep}\s+{re.escape(needle)}\b", lower):
                return city

    # 2. "<city> trip/itinerary/holiday" names the destination without a cue.
    for city, needle in cities:
        if needle == origin_lower:
            continue
        if re.search(rf"\b{re.escape(needle)}\s+(?:trip|itinerary|holiday|vacation|break)\b", lower):
            return city

    # 3. Any city mention that is neither the origin nor directly after "from".
    for city, needle in cities:
        if needle == origin_lower:
            continue
        for match in re.finditer(rf"\b{re.escape(needle)}\b", lower):
            if re.search(r"\bfrom\s+$", lower[max(0, match.start() - 8) : match.start()]):
                continue
            return city

    # 4. Unknown place names still work through the preposition patterns.
    for prep in prepositions:
        match = re.search(
            rf"\b{prep}\s+([a-z][a-z .'-]{{2,40}}?)(?=\s+(?:for|from|with|on|in|next|this|under|budget|and)\b|[,.!?]|$)",
            lower,
        )
        if match:
            candidate = match.group(1).strip(" .")
            if _valid_place(candidate) and candidate.lower() != origin_lower:
                return candidate.title()
    return None


_GENERIC_PLACES = {
    "a",
    "the",
    "my",
    "our",
    "it",
    "trip",
    "plan",
    "the plan",
    "this plan",
    "current plan",
    "the current plan",
    "itinerary",
    "the itinerary",
    "list",
    "the list",
    "day",
    "the day",
    "weather",
    "the weather",
    "hotel",
    "the hotel",
    "budget",
    "the budget",
    "same",
    "same plan",
    "above",
    "there",
    "here",
}


def _valid_place(candidate: str) -> bool:
    value = candidate.strip().lower()
    if len(value) < 3 or value in _GENERIC_PLACES:
        return False
    if value.removeprefix("the ") in _GENERIC_PLACES:
        return False
    if value in MONTHS:
        return False
    # Relative time expressions are never places: "in three months" used to be
    # parsed as the destination "Three Months".
    if re.search(
        r"\b(days?|weeks?|weekends?|months?|years?|tomorrow|today|tonight|morning|afternoon|evening)\b",
        value,
    ):
        return False
    if value in NUMBER_WORDS or value in {"a", "an", "the"}:
        return False
    return True


def _find_origin(lower: str) -> str | None:
    match = re.search(
        r"\bfrom\s+([a-z][a-z .'-]{2,40}?)(?=\s+(?:to|for|on|in|next|this|with)\b|[,.!?]|$)",
        lower,
    )
    if match:
        candidate = match.group(1).strip(" .")
        if _valid_place(candidate):
            return candidate.title()
    return None


_UNIT_WORDS = {**NUMBER_WORDS, "a": 1, "an": 1}

_CLARIFY_KEYS = {
    "start date", "start_date", "date", "dates", "travel dates",
    "end date", "end_date", "origin", "destination",
    "travellers", "travelers", "party size", "party_size", "budget",
}


def _months_later(start: date, months: int) -> date:
    index = start.month - 1 + months
    year = start.year + index // 12
    month = index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _relative_dates(lower: str, today: date) -> tuple[date | None, date | None] | None:
    """Resolve the phrases the clarify card offers, deterministically."""
    if re.search(r"\bthis weekend\b", lower):
        ahead = 0 if today.weekday() == 6 else (5 - today.weekday()) % 7
        start = today + timedelta(days=ahead)
        return start, start + timedelta(days=1)
    if re.search(r"\bnext weekend\b", lower):
        ahead = (5 - today.weekday()) % 7 + 7
        start = today + timedelta(days=ahead)
        return start, start + timedelta(days=1)
    if re.search(r"\bnext month\b", lower):
        return _months_later(today.replace(day=1), 1), None
    if re.search(r"\bnext week\b", lower):
        return today + timedelta(days=7), None

    amount = r"(one|two|three|four|five|six|seven|eight|nine|ten|a|an|\d{1,2})"
    for pattern in (
        rf"\bin\s+{amount}\s+(day|week|month|year)s?\b",
        rf"\b{amount}\s+(day|week|month|year)s?\s+from\s+now\b",
    ):
        match = re.search(pattern, lower)
        if not match:
            continue
        raw, unit = match.group(1), match.group(2)
        count = _UNIT_WORDS.get(raw, int(raw) if raw.isdigit() else 0)
        if not count:
            continue
        if unit == "day":
            return today + timedelta(days=count), None
        if unit == "week":
            return today + timedelta(days=7 * count), None
        if unit == "month":
            return _months_later(today, count), None
        return _months_later(today, 12 * count), None
    return None


def _labeled_clarify(message: str) -> dict[str, str]:
    """Parse the clarify form's "slot: value; slot: value" answer."""
    if ":" not in message:
        return {}
    found: dict[str, str] = {}
    for part in re.split(r"[;\n]", message):
        match = re.match(
            r"\s*(?:here are the missing details\s*[—–-]\s*)?([A-Za-z_ ]+?)\s*:\s*(.+?)\s*$",
            part.strip(),
            re.I,
        )
        if not match:
            continue
        key = match.group(1).strip().lower()
        if key in _CLARIFY_KEYS:
            found[key] = match.group(2).strip().rstrip(".")
    return found


def _apply_labeled(context: TripContext, labeled: dict[str, str], today: date) -> None:
    if labeled.get("origin"):
        context.origin = labeled["origin"].strip()
    if labeled.get("destination"):
        context.destination = labeled["destination"].strip()

    date_text = (
        labeled.get("dates")
        or labeled.get("date")
        or labeled.get("travel dates")
        or labeled.get("start date")
        or labeled.get("start_date")
    )
    if date_text:
        start, end = _find_dates(date_text.lower(), today)
        if start is None:
            try:
                start = date.fromisoformat(date_text[:10])
            except ValueError:
                start = None
        if start:
            context.start_date = start
            if end:
                context.end_date = end
    end_text = labeled.get("end date") or labeled.get("end_date")
    if end_text:
        try:
            context.end_date = date.fromisoformat(end_text[:10])
        except ValueError:
            pass

    party_text = (
        labeled.get("travellers")
        or labeled.get("travelers")
        or labeled.get("party size")
        or labeled.get("party_size")
    )
    if party_text:
        digits = re.search(r"\d{1,2}", party_text)
        if digits:
            context.party_size = int(digits.group())

    budget_text = labeled.get("budget")
    if budget_text:
        amount_match = re.search(r"[0-9][0-9,]*(?:\.[0-9]+)?", budget_text)
        if amount_match:
            context.budget = float(amount_match.group().replace(",", ""))
        code = re.search(
            r"\b(USD|EUR|GBP|JPY|HKD|CNY|SGD|THB|TWD|KRW|AUD|CAD)\b", budget_text, re.I
        )
        if code:
            context.currency = code.group().upper()


def _find_dates(lower: str, today: date) -> tuple[date | None, date | None]:
    relative = _relative_dates(lower, today)
    if relative:
        return relative
    iso = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", lower)
    if len(iso) >= 2:
        return date.fromisoformat(iso[0]), date.fromisoformat(iso[1])
    if len(iso) == 1:
        return date.fromisoformat(iso[0]), None

    written = _written_dates(lower, today)
    if written:
        return written

    month_only = re.search(r"\b(?:in|next|this)\s+(january|february|march|april|may|june|july|august|september|october|november|december)\b", lower)
    if month_only:
        month = MONTHS[month_only.group(1)]
        year = today.year if month >= today.month else today.year + 1
        return date(year, month, 1), None
    return None, None


#: Month words in chat dates, full or short ("nov", "sept"), with an optional full stop.
_MONTH_WORDS = {**MONTHS, **{name[:3]: number for name, number in MONTHS.items()}, "sept": 9}
_MONTH = (
    r"(january|february|march|april|may|june|july|august|september|october|november|december"
    r"|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\b\.?"
)
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s*(20\d{2})\b)?"
#: Only these words join two dates into a range, so a date in a later constraint
#: ("20 November cannot have any plan") is never taken as the trip's end.
_TO = r"\s*(?:-|–|—|to|until|till|through|and)\s*"
_DAY_RANGE_MONTH = re.compile(rf"\b{_DAY}{_TO}{_DAY}\s+{_MONTH}{_YEAR}")  # 18 to 23 November 2026
_MONTH_DAY_RANGE = re.compile(rf"\b{_MONTH}\s+{_DAY}{_TO}{_DAY}\b{_YEAR}")  # Nov 18-23, 2026
_DAY_MONTH = re.compile(rf"\b{_DAY}\s+{_MONTH}{_YEAR}")  # 18 November 2026
_MONTH_DAY = re.compile(rf"\b{_MONTH}\s+{_DAY}\b{_YEAR}")  # November 18, 2026


def _written_dates(lower: str, today: date) -> tuple[date | None, date | None] | None:
    """The first written date or date range: "18 to 23 November", "Nov 18-23", "18 Nov to 23 Nov"."""
    candidates: list[tuple[int, date, date | None]] = []
    for match in _DAY_RANGE_MONTH.finditer(lower):
        month = _MONTH_WORDS[match.group(3)]
        span = _date_span(int(match.group(1)), month, int(match.group(2)), month, match.group(4), today)
        if span:
            candidates.append((match.start(), *span))
    for match in _MONTH_DAY_RANGE.finditer(lower):
        month = _MONTH_WORDS[match.group(1)]
        span = _date_span(int(match.group(2)), month, int(match.group(3)), month, match.group(4), today)
        if span:
            candidates.append((match.start(), *span))

    mentions: list[tuple[int, int, int, int, str | None]] = []  # start, end, day, month, year
    for match in _DAY_MONTH.finditer(lower):
        mentions.append((match.start(), match.end(), int(match.group(1)), _MONTH_WORDS[match.group(2)], match.group(3)))
    for match in _MONTH_DAY.finditer(lower):
        mentions.append((match.start(), match.end(), int(match.group(2)), _MONTH_WORDS[match.group(1)], match.group(3)))
    mentions.sort()
    kept: list[tuple[int, int, int, int, str | None]] = []
    for mention in mentions:
        if not kept or mention[0] >= kept[-1][1]:
            kept.append(mention)
    for first, second in zip(kept, kept[1:]):
        if re.fullmatch(_TO, lower[first[1]:second[0]]):
            span = _date_span(first[2], first[3], second[2], second[3], second[4] or first[4], today)
            if span:
                candidates.append((first[0], *span))
    if candidates:
        _, start, end = min(candidates, key=lambda row: row[0])
        return start, end
    for _, _, day, month, year in kept:
        single = _date_span(day, month, None, None, year, today)
        if single:
            return single
    return None


def _date_span(
    start_day: int,
    start_month: int,
    end_day: int | None,
    end_month: int | None,
    year_text: str | None,
    today: date,
) -> tuple[date, date | None] | None:
    """Dates for a written day-month pair or range; a written year applies to the range's end,
    an unwritten one means the next such date from today, and a range may cross a new year."""
    crosses = end_month is not None and (end_month, end_day) < (start_month, start_day)
    try:
        if year_text:
            end_year = int(year_text)
            start = date(end_year - 1 if crosses else end_year, start_month, start_day)
        else:
            start = date(today.year, start_month, start_day)
            if start < today:
                start = date(today.year + 1, start_month, start_day)
        if end_month is None or end_day is None:
            return start, None
        end = date(start.year + 1 if crosses else start.year, end_month, end_day)
    except ValueError:
        return None
    return (start, end) if end >= start else None


def _hard_constraints(text: str) -> list[str]:
    constraints: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|;\s*", text):
        lower = sentence.lower()
        # Arrival, departure and free-day sentences are constraints the planner
        # schedules around ("we land at 18:35", "keep 19/11 free").
        if re.search(
            r"\b(must|need to|have to|avoid|cannot|can't|no |without|wheelchair|accessible"
            r"|arriv\w*|land(?:s|ing)?|depart\w*|flight|free day|rest day|day off)\b"
            r"|上機|登機|起飛|落機|到達|抵達|出機場|行程",
            lower,
        ):
            cleaned = sentence.strip()
            if 4 < len(cleaned) < 180:
                constraints.append(cleaned)
    return constraints[:5]


def _allowed_patch(before: dict) -> set[str]:
    """Only slots that were empty before this turn may be filled by the model."""
    allowed: set[str] = set()
    for key in (
        "destination",
        "origin",
        "hotel_style",
        "currency",
        "party_size",
        "days",
        "budget",
        "start_date",
        "end_date",
    ):
        if not before.get(key):
            allowed.add(key)
    allowed.update({"preferences", "hard_constraints"})
    return allowed


def _apply_model_patch(context: TripContext, data: dict, allowed: set[str] | None = None) -> None:
    allowed = allowed or {
        "destination",
        "origin",
        "hotel_style",
        "currency",
        "party_size",
        "days",
        "budget",
        "start_date",
        "end_date",
        "preferences",
        "hard_constraints",
    }

    def text_value(key: str) -> str | None:
        value = data.get(key)
        return str(value).strip() if isinstance(value, str) and value.strip() else None

    def int_value(key: str) -> int | None:
        value = data.get(key)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    for key in ("destination", "origin", "hotel_style", "currency"):
        if key not in allowed:
            continue
        value = text_value(key)
        if value:
            setattr(context, key, value)
    if "party_size" in allowed:
        party = int_value("party_size")
        if party and 1 <= party <= 20:
            context.party_size = party
    if "days" in allowed:
        days = int_value("days")
        if days and 1 <= days <= 60:
            context.days = days
    if "budget" in allowed:
        budget = data.get("budget")
        if isinstance(budget, (int, float)) and budget >= 0:
            context.budget = float(budget)
    for key in ("start_date", "end_date"):
        if key not in allowed:
            continue
        value = data.get(key)
        if isinstance(value, str):
            try:
                setattr(context, key, date.fromisoformat(value))
            except ValueError:
                pass
    for key in ("preferences", "hard_constraints"):
        values = data.get(key)
        if isinstance(values, list):
            existing = getattr(context, key)
            for item in values[:8]:
                if isinstance(item, str) and item.strip() and item.strip() not in existing:
                    existing.append(item.strip())


def _normalise_dates(context: TripContext) -> None:
    if context.start_date and not context.end_date and context.days:
        context.end_date = context.start_date + timedelta(days=max(context.days - 1, 0))
    if context.start_date and context.end_date:
        context.days = max((context.end_date - context.start_date).days + 1, 1)
    elif context.start_date and not context.days:
        context.days = 1
