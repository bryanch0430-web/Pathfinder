"""Turn a message into trip slots.

Two channels: deterministic extraction (always available, auditable) and the
writer model (fills what the rules cannot). The model may leave a slot null; it
may never overwrite a slot the traveller did not mention with a guess that
contradicts an existing value.
"""

from __future__ import annotations

import re
import calendar
from bisect import bisect_left
from datetime import date, timedelta
from typing import Sequence

from app.application.llm_gateway import LLMGateway
from app.application.prompt_context import conversation_block, trip_state, with_conversation
from app.domain.errors import CanaryLeak
from app.domain.models import Message, TripContext, fit_preference
from app.infrastructure.providers.flights import AIRPORTS
from app.security import fence_user_message

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
    history: Sequence[Message] = (),
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
        # What this step extracts is stored with the trip and in the profile: it reads the traveller's own earlier
        # turns and only the last assistant turn (the question the message may answer), never earlier answers.
        conversation = conversation_block(history, message, last_assistant_only=True)
        prompt = (
            f"Current date: {today.isoformat()}\n"
            f"Existing context: {trip_state(updated)}\n"
            f"Still missing: {missing}\n\n"
            + with_conversation(conversation)
            + "Traveller message:\n" + fence_user_message(message)
        )
        try:
            data = await gateway.json("understand", user=prompt, purpose="understand")
            _apply_model_patch(updated, _not_copied_from(data, conversation, message), allowed=_allowed_patch(before))
        except CanaryLeak:
            raise
        except Exception:
            # Deterministic extraction stands on its own.
            pass

    _normalise_dates(updated)
    _apply_confirmations(message, labeled, context, updated)
    return updated


#: "plan anyway", "Keep HKD 5,000 and plan anyway", "go ahead anyway": the traveller keeps what the check before
#: planning asked about (budget_check).
_ANYWAY = re.compile(r"\b(?:plan|go ahead|proceed|continue|keep)\b[^.;!?]{0,60}?\banyway\b", re.I)


def _apply_confirmations(message: str, labeled: dict[str, str], before: TripContext, updated: TripContext) -> None:
    """Mark what the traveller chose to keep, and clear a mark that another budget or start date no longer backs."""
    if (updated.budget, updated.currency.upper()) != (before.budget, before.currency.upper()):
        updated.budget_confirmed = False
    if updated.start_date != before.start_date:
        updated.past_dates_confirmed = False
    if labeled:
        if _ANYWAY.search(labeled.get("budget", "")):
            updated.budget_confirmed = True
        dates = " ".join(labeled.get(key, "") for key in ("dates", "date", "travel dates", "start date", "start_date"))
        if _ANYWAY.search(dates):
            updated.past_dates_confirmed = True
    elif _ANYWAY.search(message):
        updated.budget_confirmed = True
        updated.past_dates_confirmed = True


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

    # budget: currency symbol or code plus amount. Blanks are matched only after a word or a symbol, and a
    # number is read from its first digit, so a message of blanks or digits cannot make the patterns backtrack.
    budget_match = re.search(
        r"(?:(?:budget|under|below|max(?:imum)?|around|about|spend)\s*)?"
        r"(?:(HK\$|S\$|[$€£¥฿])|(USD|EUR|GBP|JPY|HKD|CNY|SGD|THB|TWD|KRW|AUD|CAD))\s*"
        r"([0-9][0-9,]*(?:\.[0-9]+)?)",
        text,
        re.IGNORECASE,
    )
    if not budget_match:
        budget_match = re.search(
            r"(?<![0-9])(?<![0-9],)([0-9][0-9,]*(?:\.[0-9]+)?)\s*(USD|EUR|GBP|JPY|HKD|CNY|SGD|THB|TWD|KRW|AUD|CAD)\b",
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
    named = _find_place(lower, _PLACE_CUES)
    origin = _find_origin(text, context.origin, context.destination, named or "")
    if origin:
        context.origin = origin
    destination = _find_place(lower, _PLACE_CUES, origin) if origin else named
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


#: The words after which a place in a sentence is read as the destination.
_PLACE_CUES = ["to", "in", "visit", "explore", "around"]


def _find_place(lower: str, prepositions: list[str], origin: str | None = None) -> str | None:
    """Resolve the destination, never the departure city.

    Order matters: an explicit destination cue ("to Kyoto", "Kyoto trip") beats a
    bare mention anywhere else in the sentence, and the origin is excluded at
    every step. Otherwise "a Kyoto trip from Hong Kong" resolves to Hong Kong,
    because a bare city scan finds the origin first. Within a step the place the
    sentence names first wins, whichever cue comes first in the list: "4 days in
    Kyoto with a day trip to Osaka" is Kyoto.
    """
    origin_lower = (origin or "").strip().lower()
    cities = [(city, city.lower()) for city in CITY_HINTS if city.lower() != origin_lower]

    # 1. Explicit destination cue: "<to|in|visit|explore|around> <city>".
    cued = [
        (_side_mention(lower, match.start()), match.start(), city)
        for prep in prepositions
        for city, needle in cities
        if (match := re.search(rf"\b{prep}\s+{re.escape(needle)}\b", lower))
    ]
    if cued:
        return min(cued)[2]

    # 2. "<city> trip/itinerary/holiday" names the destination without a cue.
    named = [
        (match.start(), city)
        for city, needle in cities
        if (match := re.search(rf"\b{re.escape(needle)}\s+(?:trip|itinerary|holiday|vacation|break)\b", lower))
    ]
    if named:
        return min(named)[1]

    # 3. Any city mention that is neither the origin nor directly after "from".
    mentioned: list[tuple[int, str]] = []
    for city, needle in cities:
        for match in re.finditer(rf"\b{re.escape(needle)}\b", lower):
            if re.search(r"\bfrom\s+$", lower[max(0, match.start() - 8) : match.start()]):
                continue
            mentioned.append((match.start(), city))
            break
    if mentioned:
        return min(mentioned)[1]

    # 4. Unknown place names still work through the preposition patterns.
    unknown: list[tuple[bool, int, str]] = []
    for prep in prepositions:
        pattern = re.compile(
            rf"\b{prep}\s+([a-z][a-z .'-]{{2,40}}?)(?=\s+(?:for|from|with|on|in|next|this|under|budget|and)\b|[,.!?]|$)"
        )
        start = 0
        while (match := pattern.search(lower, start)) is not None:
            candidate = _until_name_ends(match.group(1).strip(" ."))
            first_word = candidate.split()[0].lower() if candidate else ""
            if (
                _valid_place(candidate)
                and candidate.lower() != origin_lower
                and not (prep == "to" and first_word in _VERBS_AFTER_TO)
                and first_word not in prepositions
            ):
                unknown.append((_side_mention(lower, match.start()), match.start(), candidate.title()))
                break
            start = match.start() + 1  # "want to go to Porto": the first "to" is no cue, the next one is
    return min(unknown)[2] if unknown else None


def _side_mention(lower: str, position: int) -> bool:
    """Whether the cue at `position` follows "stopover", "layover" or "live in": "Stopover in Dubai on the way to
    Sydney" and "I live in London and want to go to Rome" are trips to Sydney and to Rome, and Dubai and London are the
    destination only when the sentence names no other place."""
    return re.search(r"\b(?:stop-?over|layover|stop|live|lives|living|based|born|grew up)\s+$", lower[max(0, position - 12) : position]) is not None


#: A verb that follows "to" in "love to see Lyon" (not after "in" or "visit": "3 days in Do Son"): the place comes after it,
#: so it is not part of the name.
_VERBS_AFTER_TO = frozenset(
    "visit see explore go travel fly stay spend try get be have do take discover check tour enjoy relax drink walk eat "
    "sleep swim hike shop wait stop start finish cook order call scan confirm ask say tell show know make find pick "
    "keep join meet send live work move study retire relocate".split()
)


def _until_name_ends(phrase: str) -> str:
    """The words of `phrase` before the first one that ends a place name: "nagoya please" is Nagoya."""
    words: list[str] = []
    for word in phrase.split():
        if word.lower().strip(".,!?'-") in _NAME_ENDS:
            break
        words.append(word)
    return " ".join(words)


_GENERIC_PLACES = {
    "now",
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
    # nouns and words that follow "in" or "to" and are no place: "6 days in total", "in the city", "in winter", "in euros"
    "total",
    "city",
    "town",
    "area",
    "country",
    "general",
    "advance",
    "end",
    "middle",
    "rest",
    "old town",
    "city centre",
    "city center",
    "centre",
    "center",
    "downtown",
    "suburbs",
    "countryside",
    "station",
    "airport",
    "winter",
    "summer",
    "spring",
    "autumn",
    "fall",
    "euro",
    "euros",
    "dollar",
    "dollars",
    "pound",
    "pounds",
    "yen",
    "english",
    "french",
    "german",
    "spanish",
    "italian",
    "japanese",
    "chinese",
    "korean",
}


#: Whole names that are no place: a weekday ("from Monday to Friday"), home, work and "from scratch".
_NOT_A_PLACE = frozenset("monday tuesday wednesday thursday friday saturday sunday home work scratch".split())


def _valid_place(candidate: str) -> bool:
    value = candidate.strip().lower()
    if len(value) < 3 or value in _GENERIC_PLACES:
        return False
    if value.removeprefix("the ") in _GENERIC_PLACES:
        return False
    if value in MONTHS or value in _NOT_A_PLACE:
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


#: The apostrophes a name may hold: the typewriter one, the curly ones a phone types and the Hawaiian okina ("Coeur d’Alene").
_APOSTROPHES = "'’‘ʼʻ"
_ASCII_APOSTROPHE = str.maketrans(_APOSTROPHES, "'" * len(_APOSTROPHES))
#: A name may begin with an abbreviation and its full stop, which is no end of a sentence: "St. Louis", "Ft. Lauderdale".
_NAME_START = r"(?:(?:st|ft|mt)\.\s*)?"
#: "from <words>": the words end where the place name ends (`_NAME_ENDS`), which the pattern cannot say.
_FROM_PHRASE = re.compile(rf"\bfrom\s+({_NAME_START}[a-z][a-z {_APOSTROPHES}-]{{1,40}})", re.IGNORECASE)
#: The words that end a place name: a conjunction, a preposition or a word about the time ("Leeds via Dubai",
#: "Chicago and staying in Rome", "Leeds around noon").
_NAME_ENDS = frozenset(
    "and or but via at by around then after before while because so since if when as until till which that where "
    "to for on in into onto next this with without towards toward near than from only just please thanks instead "
    "again also too maybe about during over under through now today tomorrow tonight soon not".split()
)
#: Words that do not continue a place name once it has begun: a pronoun, an auxiliary, a determiner, an adverb, a number
#: or a weekday ("Leeds we are 4 people", "London is out", "Paris direct", "Leeds Friday"). A name may begin with one
#: ("Can Tho"). The words are checked after the first one of a name only.
_NOT_A_CONTINUATION = frozenset(
    (
        "i me we us you he she they it my our your his her their its the a an this that these those is are was were be am "
        "will would can could shall should must might do does did have has had want wants need needs plan plans direct "
        "directly nonstop round one two three four five six seven eight nine ten "
        "monday tuesday wednesday thursday friday saturday sunday"
    ).split()
)
#: Cities and airports the assistant knows by name, and the airport codes the flight source resolves.
_KNOWN_CITIES = {city.lower(): city for city in CITY_HINTS}
_AIRPORT_CODES = frozenset(code for codes in AIRPORTS.values() for code in codes)
#: Words right before "from" that say the traveller leaves from the place after it ...
_DEPARTURE_VERBS = frozenset(
    "fly flying flew flown depart departing departure departures leave leaving left commute commuting travelling "
    "traveling coming arrive arriving originate originating setting heading".split()
)
#: ... or that a flight, a train or someone else does ("the bus leaves from Shinjuku"), or the traveller is there at the
#: start or on the way back ("starting from Kyoto", "returning from Tokyo"). The city is then a guess, so it is
#: a departure city for a trip that has none, and never replaces the one a trip has.
_THIRD_PERSON = frozenset("departs leaves commutes travels comes arrives originates".split())
_WEAK_VERBS = frozenset("starting returning returned".split())
_TRAVEL_WORDS = _DEPARTURE_VERBS | _THIRD_PERSON | _WEAK_VERBS
_RETURN_WORDS = frozenset("return returning returned back".split())
#: Words between such a verb and "from" that say nothing more ("flying out from", "fly back from").
_VERB_FILLERS = frozenset(
    "out off in back straight directly now again only just also usually always soon together away then all still".split()
)
#: "flights from Osaka" is a departure; "a flight from Osaka to Tokyo on day 3" and "the flight from Osaka" are not.
_FLIGHT_NOUNS = frozenset(["flight", "flights"])
_DETERMINERS = frozenset(
    "a an the this that these those our my your his her their one any each every another same which what".split()
)
#: A trip word means the sentence is about a trip, unless it is a short one from the place the traveller stays in.
_TRIP_WORDS = frozenset("trip trips journey holiday vacation getaway honeymoon".split())
_SHORT_TRIPS = frozenset("day side half-day one-day 1-day same-day overnight".split())
_MANY_DAYS = frozenset("two three four five six seven eight nine ten".split())
#: Words that say that something in the plan is being changed, which "from Kyoto" then describes.
_EDIT_WORDS = frozenset("change swap switch replace update move shift modify edit redo adjust".split())
#: A planning request ("Plan Kyoto from Singapore") and a stay ("a week in Paris from New York") give a trip with no
#: departure city one it knows.
_PLAN_VERBS = frozenset("plan planning book organise organize arrange create build design generate prepare".split())
_STAY = re.compile(
    r"\b(?:[2-9]|\d\d|two|three|four|five|six|seven|eight|nine|ten)[\s-]*(?:days?|nights?|weeks?)\b"
    r"|\b(?:a|an|one)[\s-]*(?:week|fortnight)\b|\bweekend\b",
    re.IGNORECASE,
)
#: An explicit change of the departure city: "Actually, plan it from Singapore", "Start from Singapore instead".
_CHANGE_CUE = re.compile(r"\b(?:actually|instead|rather)\b", re.IGNORECASE)
_CUE_FILLERS = frozenset("actually instead rather please ok okay so well now just then and but also sorry no yes".split())
_AT_THE_START = frozenset("start begin go plan planning".split())
_THE_TRIP = frozenset("it this that everything again over things".split())
#: The first word of a sentence that asks something, and of one that asks the assistant to do something.
_QUESTION_STARTS = frozenset(
    "how what what's whats where when which who whose why is are was were do does did has have had can could would "
    "will should shall may might".split()
)
_ASKING_TO_DO = frozenset("can could would will shall".split())
_LEAD_FILLERS = frozenset("and but so also please actually hi hello hey ok okay well then oh um uh yes yeah sure thanks".split())
#: A place name does not begin with one of these.
_NOT_A_NAME_START = frozenset(
    "the a an my our your this that these those his her their its some any each every".split()
)
#: A name that is a day or a month is no city to leave from, but a word of a longer name may be one ("Cape May", "Sun City").
_NOT_A_DAY = frozenset(
    (
        "monday mon tuesday tue tues wednesday wed thursday thu thur thurs friday fri saturday sat sunday sun "
        "january jan february feb march mar april apr may june jun july jul august aug september sept sep october oct "
        "november nov december dec"
    ).split()
)
#: A name that is, or begins or ends with, a home or work, a terminal or an airline, or says nothing, is no city to leave from.
_NOT_AN_ORIGIN = frozenset(
    (
        "home work office school scratch terminal gate airline airlines airways carrier carriers abroad overseas "
        "somewhere anywhere everywhere nowhere here there noon midnight unknown none tbd tba flexible same different"
    ).split()
)
#: The last word of a name that is a building, a part of a city or what comes from one, not a city.
_NOT_A_CITY_ENDS = frozenset(
    "station tower castle temple shrine hotel hostel museum market park bridge street road avenue square palace mall "
    "centre center hospital university stadium harbour harbor pier beach cuisine style food prices guide insurance".split()
)
#: Of those, the words that end the name of a city too when a name the assistant does not know comes before them.
_CITY_NAME_ENDS = frozenset("beach station harbor harbour park".split())
#: A departure city that is stated: "my departure city is Osaka", "change the origin to Osaka", "use Osaka as the origin".
_ORIGIN_NOUN = r"(?:origin|departure\s+(?:city|airport|point)|home\s+(?:city|airport)|starting\s+(?:point|city))"
_NAME = rf"({_NAME_START}[a-z][a-z {_APOSTROPHES}-]{{1,40}})"
_STATED_ORIGIN = (
    re.compile(rf"\b{_ORIGIN_NOUN}\s*(?:(?:is|are|will be|should be)\b|[=:])\s*{_NAME}", re.IGNORECASE),
    re.compile(rf"\b(?:change|set|switch|update)\s+(?:the\s+|my\s+|our\s+)?{_ORIGIN_NOUN}\s+(?:to|as)\s+{_NAME}", re.IGNORECASE),
    re.compile(rf"\buse\s+({_NAME_START}[a-z][a-z {_APOSTROPHES}-]{{1,40}}?)\s+as\s+(?:the\s+|my\s+|our\s+)?{_ORIGIN_NOUN}\b", re.IGNORECASE),
)
_SENTENCE_END = re.compile(r"[.!?;]")
#: How much of the sentence before "from" is read, which keeps the work in proportion to the message.
_LOOKBACK = 300
#: A day of the plan, said after a "from" phrase: "on day 1".
_ON_A_DAY = re.compile(r"\bday\s*\d", re.IGNORECASE)
#: "to <place>" right after a "from" phrase: "a 3-day trip to Kyoto from Singapore to Tokyo".
_ONWARD = re.compile(r"\s+to\s+([a-z][a-z .'-]{1,40})", re.IGNORECASE)


def _known_place(candidate: str) -> str | None:
    """The city or airport that `candidate` names, when the assistant knows it: "Hong Kong airport" is Hong
    Kong, but "Kyoto Station" and "Kyoto cuisine" are no city."""
    words = candidate.split()
    if len(words) == 1 and words[0] in _AIRPORT_CODES:
        return words[0]  # an airport code, typed in capitals
    lowered = [word.lower() for word in words]
    for size in range(len(lowered), 0, -1):
        city = _KNOWN_CITIES.get(" ".join(lowered[:size]))
        if city:
            rest = lowered[size:]
            return city if not rest or (rest[-1] == "airport" and len(rest) <= 2) else None
    return None


def _known_prefix(lowered: list[str]) -> str | None:
    """The longest city the assistant knows that the words begin with: "New York" for "new york jfk"."""
    for size in range(len(lowered), 0, -1):
        city = _KNOWN_CITIES.get(" ".join(lowered[:size]))
        if city:
            return city
    return None


def _name_words(phrase: str) -> tuple[list[str], int, bool]:
    """The words of `phrase` up to the first one that ends a place name, how many characters they take, and whether
    a word that ends a name was found (else the phrase may stop in the middle of a word)."""
    words: list[str] = []
    used = 0
    for word in re.finditer(r"\S+", phrase):
        token = word.group().lower()
        if token in _NAME_ENDS or (words and token in _NOT_A_CONTINUATION) or not any(char.isalpha() for char in token):
            return words, used, True
        words.append(word.group().translate(_ASCII_APOSTROPHE).strip("'-"))
        used = word.end()
    return words, used, False


def _origin_name(words: list[str]) -> tuple[str, bool] | None:
    """The departure city that `words` name and whether the assistant knows it; None when they name no city."""
    if not words or len(words) > 4:
        return None
    name = " ".join(words)
    lowered = [word.lower() for word in words]
    if not _valid_place(name) or lowered[0] in _NOT_A_NAME_START or lowered[0] in NUMBER_WORDS:
        return None
    if lowered[0] in _NOT_AN_ORIGIN or lowered[-1] in _NOT_AN_ORIGIN or lowered == ["airport"] or name.lower() in _NOT_A_DAY:
        return None
    known = _known_place(name)
    if known:
        return known, True
    city = _known_prefix(lowered)
    if city:  # "London Heathrow", "New York JFK" are the city; "Kyoto Station" and "Tokyo Tower" are no city to leave from
        return (city, True) if lowered[-1] not in _NOT_A_CITY_ENDS else None
    if lowered[-1] in _NOT_A_CITY_ENDS and (len(words) == 1 or lowered[-1] not in _CITY_NAME_ENDS):
        return None  # "Station" alone and "Boston Hotel" are no city; "Long Beach", "Bar Harbor" and "College Station" are
    return name.title(), False


def _stated_origin(text: str) -> str | None:
    """The city a sentence says is the departure city: "my departure city is Osaka", "change the origin to Osaka"."""
    for pattern in _STATED_ORIGIN:
        for match in pattern.finditer(text):
            place = _origin_name(_name_words(match.group(1))[0])
            if place:
                return place[0]
    return None


def _words(text: str) -> list[str]:
    return [word.strip(",:()\"'").lower() for word in text.replace("\u2019", "'").split()]


def _is_request(lead: list[str]) -> bool:
    """Whether a sentence that begins with `lead` asks the assistant to do something ("can you plan Kyoto from Osaka?")."""
    words = [word for word in lead if word not in _LEAD_FILLERS]
    return bool(words) and words[0] in _ASKING_TO_DO and words[1:2] == ["you"]


def _is_question(lead: list[str]) -> bool:
    """Whether a sentence that begins with `lead` asks something ("how far is the hotel from the station?")."""
    words = [word for word in lead if word not in _LEAD_FILLERS]
    return bool(words) and words[0] in _QUESTION_STARTS and not _is_request(lead)


def _departing(before: list[str], *, through_a_place: bool = True) -> str | None:
    """How the words before "from" say that the traveller leaves: "leave" for a travel verb or "flights" right before
    it or before a short place ("flights to Rome from", "arriving in Rome from"; not when `through_a_place` is False),
    "return" for a start or a way back ("returning from", "fly back from"), "guess" for where the traveller is from or
    starts ("we start from", "I'm from", "by train from"), "other" for a verb that is about a flight, a train or another
    person ("it leaves from"), None for none."""
    said = _departing_right_before(before)
    for size in range(2, 5):  # a preposition and up to three words of a place: "flights to Rome from London"
        if said is None and through_a_place and len(before) >= size and before[-size] in _GAP_PREPOSITIONS:
            said = _departing_right_before(before[:-size], gap=True)
    return said


#: What may stand between a flight or a travel verb and "from": "flights to Rome from", "arriving in Rome from".
_GAP_PREPOSITIONS = frozenset("to into in".split())
_INDEFINITE = frozenset("a an one any another".split())
#: Verbs that say the traveller leaves only through a place ("travel to Rome from London"), not right before "from"
#: ("Travel from Osaka to Nara on the second day" tells what to do on a day).
_ONLY_THROUGH_A_PLACE = frozenset(["travel"])
_STARTS = frozenset("start starts begin begins beginning".split())
_THE_TRAVELLER = frozenset("i we they trip journey tour holiday vacation".split())
_BY = frozenset("by".split())
_VEHICLES = frozenset("train car bus coach ferry".split())


def _departing_right_before(before: list[str], *, gap: bool = False) -> str | None:
    index = len(before) - 1
    while index >= 0 and before[index] in _VERB_FILLERS and len(before) - 1 - index < 2:
        index -= 1
    if index < 0:
        return None
    word = before[index]
    if word in _FLIGHT_NOUNS:
        if index and before[index - 1] in _DETERMINERS and not (gap and before[index - 1] in _INDEFINITE):
            return None
    elif word in _STARTS:  # "we start from", "our trip starts from"
        return "guess" if index and before[index - 1] in _THE_TRAVELLER else None
    elif word in {"plane", "air"} and index and before[index - 1] in _BY:
        return "leave"
    elif word in _VEHICLES and index and before[index - 1] in _BY:
        return "guess"
    elif word in {"i'm", "we're"} or (index and (before[index - 1], word) in {("i", "am"), ("we", "are")}):
        return "guess"  # "I'm from London": where the traveller is from, not for certain where the trip leaves from
    elif word == "set" and before[index + 1 : index + 2] == ["off"]:  # "we set off from Paris"
        return "leave"
    elif word not in _TRAVEL_WORDS and not (gap and word in _ONLY_THROUGH_A_PLACE):
        return None
    if word in _WEAK_VERBS or any(back in _RETURN_WORDS for back in before[max(index - 1, 0) :]):
        return "return"
    return "other" if word in _THIRD_PERSON else "leave"


def _short_trip(before: list[str], index: int) -> bool:
    """Whether the trip word at `index` is a short trip from the place the traveller stays in ("a day trip from Kyoto")."""
    if index == 0 or before[index - 1] not in _SHORT_TRIPS:
        return False
    many = index > 1 and (before[index - 2] in _MANY_DAYS or (before[index - 2].isdigit() and int(before[index - 2]) > 1))
    return not (before[index - 1] == "day" and many)  # "a 5 day trip" is not


#: A comma, a colon or a dash with blanks ends a clause: "For our trip, souvenirs from Osaka are a must".
_CLAUSE_BREAK = re.compile(r"[,:;]|\s[-\u2013\u2014]+\s")
#: A trip word names the trip a "from" phrase is about only within this many words before it ("a 3-day trip to Kyoto from X").
_TRIP_REACH = 6


def _clause_words(window: str) -> list[str]:
    """The words of `window` since its last comma, colon or dash: the clause the "from" phrase that follows is in."""
    last = None
    for last in _CLAUSE_BREAK.finditer(window):
        pass
    return _words(window[last.end() :] if last else window)


def _trips(before: list[str]) -> tuple[bool, bool, bool]:
    """Whether `before` names a trip, names one in its last two words, and names a short trip from a place."""
    trip = near = short = False
    for index, word in enumerate(before):
        if word in _TRIP_WORDS:
            if _short_trip(before, index):
                short = True
            else:
                trip = True
                near = near or index >= len(before) - 2
    return trip, near, short


def _changes_the_trip(before: list[str]) -> bool:
    """Whether the words before "from" are about the trip as a whole ("plan it", "start", "make it") and not about a
    part of it ("show me restaurants", "pick a hotel")."""
    words = [word for word in before if word not in _CUE_FILLERS]
    if not words or words[-1] in _AT_THE_START:
        return True
    if len(words) > 1 and words[-1] in _THE_TRIP and words[-2] in (_AT_THE_START | {"make", "do"}):
        return True
    return any(word in {"plan", "planning"} for word in words[:6])


def _evidence(text: str, match: re.Match[str], used: int, ends: list[int], known: bool) -> str | None:
    """What the sentence around a "from" phrase says about the place after it: "replace" when it is the departure city
    whatever the trip has; "fill" when it is the departure city of a trip that has none, if the assistant knows the
    place; "fill any" when that holds for a place of any name; None when it is not the departure city."""
    index = bisect_left(ends, match.start())
    start = ends[index - 1] + 1 if index else 0
    stop = ends[index] + 1 if index < len(ends) else len(text)
    lead = _words(text[start : min(match.start(), start + _LOOKBACK)])[:6]
    if _is_question(lead):
        return None
    window = text[max(start, match.start() - _LOOKBACK) : match.start()]
    before = _words(window)
    clause = _clause_words(window)
    if _is_question(clause[:6]):  # "Planning a trip, is it cheaper to fly from London or Paris?"
        return None
    # "We arrive in Kyoto from Osaka on day 1" is a day of the plan; "flights to Rome from London" is a departure.
    departing = _departing(before, through_a_place=_ON_A_DAY.search(text, match.start(), match.end() + 80) is None)
    if departing == "leave":
        return "replace"
    # A trip word counts when it is near the "from" phrase in its clause; "we are from X" is weak by itself and takes
    # the trip word of the sentence ("A trip to Rome, we are from Singapore").
    trip, near, short = _trips(before if departing == "guess" else clause[-_TRIP_REACH:])
    if any(word in _EDIT_WORDS for word in before[-4:]) or short:
        return None
    asks = index < len(ends) and text[ends[index]] == "?" and not _is_request(lead)  # the sentence ends with a ?
    if departing == "return":
        return None if asks else "fill any"
    if asks:  # a question asks about a city; it does not say where the trip leaves from
        return None
    if departing in (None, "guess"):
        onward = _ONWARD.match(text, match.start(1) + used)
        goes_to_a_known_city = bool(onward and _known_place(" ".join(_name_words(onward.group(1))[0])))
        if trip and (known or (near and goes_to_a_known_city)):
            return "replace"
        window = (max(start, match.start() - _LOOKBACK), min(stop, match.end() + _LOOKBACK))
        if known and _CHANGE_CUE.search(text, *window) and _changes_the_trip(before):
            return "replace"
    if departing == "guess":
        return "fill any"
    leading = [word for word in before if word not in _LEAD_FILLERS]
    if departing == "other" or any(word in _PLAN_VERBS for word in clause[:6]) or _STAY.search(" ".join(before)) or not leading:
        return "fill"
    return None


def _find_origin(text: str, stored: str = "", destination: str = "", named: str = "") -> str | None:
    """The departure city that `text` names, or None.

    A sentence names it when it says so ("my departure city is Osaka", "change the origin to Osaka"); or a travel verb
    or "flights" comes right before "from" ("flying from", "leaving from", "flights from"); or the sentence is about a
    trip ("a 5-day trip from Leeds to Paris"). Those, and an explicit change ("Actually, plan it from Singapore",
    "start from Singapore instead"), replace the departure city a trip has. A planning request ("Plan Kyoto from
    Singapore"), a stay ("a week in Paris from New York"), a way back ("returning from Tokyo") and a bare "from Hong
    Kong" only give a trip that has none a city the assistant knows, never the destination, and a question never gives
    one ("how far is the temple from Kyoto Station?"). Nothing else after "from" is a departure city: not "scratch",
    a weekday, "home", an airline, a station, nor "a day trip from Kyoto to Nara". The name ends at a conjunction or
    a preposition ("Chicago and staying in Rome" is Chicago). It is never the destination the trip has (`destination`)
    or the only place the message names (`named`): "flights from Kyoto on day 5" on a trip to Kyoto, and "4 days in
    Rome, returning from Rome", name none; "I live in Osaka, flights from Osaka to Leeds" leaves from Osaka.
    """
    stated = _stated_origin(text)
    if stated:
        return stated
    ends = [match.start() for match in _SENTENCE_END.finditer(text)]
    only_place: bool | None = None  # whether `named` is the one place the message names, worked out when a phrase names it
    for match in _FROM_PHRASE.finditer(text):
        words, used, ended = _name_words(match.group(1))
        # A name that stops inside a word ("Montr", before an accent) is not a name that was read.
        if not ended and match.group(1)[-1].isalpha() and text[match.end(1) : match.end(1) + 1].isalnum():
            continue
        after = match.start(1) + used
        if len(words) > 1 and words[-1].lower() in _MONTH_WORDS and text[after : after + 4].strip()[:1].isdigit():
            words = words[:-1]  # "Boston May 3": the date is not part of the name
        place = _origin_name(words)
        if place is None:
            continue
        name, known = place
        if name.lower() == destination.strip().lower():
            continue
        if named and name.lower() == named.lower():
            if only_place is None:
                only_place = _find_place(text.lower(), _PLACE_CUES, name) is None
            if only_place:
                continue
        evidence = _evidence(text, match, used, ends, known)
        if evidence == "replace":
            return name
        if (evidence == "fill any" or (evidence == "fill" and known)) and not stored.strip():
            return name
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
_NUMERIC_YEAR = r"(?:/(20\d{2}|\d{2}))?(?![\d/:])"
#: Fractions, counts and 24/7 are not dates: "a 1/2 day", "2/3 people", "3/4 of the budget".
_NOT_A_DATE = r"(?!\s*(?:days?|nights?|hours?|hrs?|weeks?|people|persons?|pax|adults?|kids?|children|of)\b)"
_NUMERIC_DATE = re.compile(
    rf"(?<![\d/.:])(?!24/7(?![/\d]))(\d{{1,2}})/(\d{{1,2}}){_NUMERIC_YEAR}{_NOT_A_DATE}"
)  # 18/11, 18/11/2026, 18/11/26
_NUMERIC_DAY_RANGE = re.compile(
    rf"(?<![\d/.:])(\d{{1,2}}){_TO}(\d{{1,2}})/(\d{{1,2}}){_NUMERIC_YEAR}{_NOT_A_DATE}"
)  # 18-23/11


def _written_dates(lower: str, today: date) -> tuple[date | None, date | None] | None:
    """The first written date or date range: "18 to 23 November", "Nov 18-23", "18/11 to 23/11".

    The earliest one wins, a range over a single date at the same place, so a date in a
    later constraint ("20 to 21 November must stay free") never replaces the trip's start."""
    candidates: list[tuple[int, int, date, date | None]] = []  # position, 0 for a range, start, end
    for match in _DAY_RANGE_MONTH.finditer(lower):
        month = _MONTH_WORDS[match.group(3)]
        span = _date_span(int(match.group(1)), month, int(match.group(2)), month, match.group(4), today)
        if span:
            candidates.append((match.start(), 0, *span))
    for match in _MONTH_DAY_RANGE.finditer(lower):
        month = _MONTH_WORDS[match.group(1)]
        span = _date_span(int(match.group(2)), month, int(match.group(3)), month, match.group(4), today)
        if span:
            candidates.append((match.start(), 0, *span))
    for match in _NUMERIC_DAY_RANGE.finditer(lower):
        month = int(match.group(3))
        span = _date_span(
            int(match.group(1)), month, int(match.group(2)), month, _full_year(match.group(4)), today
        )
        if span:
            candidates.append((match.start(), 0, *span))

    mentions: list[tuple[int, int, int, int, str | None]] = []  # start, end, day, month, year
    for match in _DAY_MONTH.finditer(lower):
        mentions.append((match.start(), match.end(), int(match.group(1)), _MONTH_WORDS[match.group(2)], match.group(3)))
    for match in _MONTH_DAY.finditer(lower):
        mentions.append((match.start(), match.end(), int(match.group(2)), _MONTH_WORDS[match.group(1)], match.group(3)))
    for match in _NUMERIC_DATE.finditer(lower):
        pair = _numeric_day_month(int(match.group(1)), int(match.group(2)))
        if pair:
            mentions.append((match.start(), match.end(), *pair, _full_year(match.group(3))))
    mentions.sort()
    kept: list[tuple[int, int, int, int, str | None]] = []
    for mention in mentions:
        if not kept or mention[0] >= kept[-1][1]:
            kept.append(mention)
    for first, second in zip(kept, kept[1:]):
        if re.fullmatch(_TO, lower[first[1]:second[0]]):
            span = _date_span(first[2], first[3], second[2], second[3], second[4] or first[4], today)
            if span:
                candidates.append((first[0], 0, *span))
    for position, _, day, month, year in kept:
        single = _date_span(day, month, None, None, year, today)
        if single:
            candidates.append((position, 1, *single))
    if not candidates:
        return None
    _, _, start, end = min(candidates, key=lambda row: row[:2])
    return start, end


def _numeric_day_month(first: int, second: int) -> tuple[int, int] | None:
    """Day and month of 18/11, read day first as in Hong Kong; 11/18 only when that is impossible."""
    if 1 <= second <= 12 and 1 <= first <= 31:
        return first, second
    if 1 <= first <= 12 and 1 <= second <= 31:
        return second, first
    return None


def _full_year(text: str | None) -> str | None:
    """2026 for "26" or "2026"; None when no year was written."""
    if not text:
        return None
    return text if len(text) == 4 else f"20{text}"


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


def _plain(text: str) -> str:
    return " ".join(text.split()).casefold()


def _not_copied_from(data: dict, conversation: str, message: str) -> dict:
    """The model's reply less any preference or constraint copied from the earlier conversation.

    A line whose text is in the earlier turns but not in the message is history, not something the traveller asks
    for now: an answer's restaurant names, an old wish taken back. A paraphrase of the message still passes. With no
    earlier conversation the reply is returned as it is.
    """
    if not conversation or not isinstance(data, dict):
        return data
    earlier, said = _plain(conversation), _plain(message)
    kept = dict(data)
    for key in ("preferences", "hard_constraints"):
        values = data.get(key)
        if isinstance(values, list):
            kept[key] = [
                item
                for item in values
                if not (isinstance(item, str) and _plain(item) and _plain(item) in earlier and _plain(item) not in said)
            ]
    return kept


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
            # A preference ends up in the traveller's profile, which keeps no phrase longer than a tag.
            fit = fit_preference if key == "preferences" else str.strip
            for item in values[:8]:
                text = fit(item) if isinstance(item, str) else ""
                if text and text not in existing:
                    existing.append(text)


def _normalise_dates(context: TripContext) -> None:
    if context.start_date and not context.end_date and context.days:
        context.end_date = context.start_date + timedelta(days=max(context.days - 1, 0))
    if context.start_date and context.end_date:
        context.days = max((context.end_date - context.start_date).days + 1, 1)
    elif context.start_date and not context.days:
        context.days = 1
