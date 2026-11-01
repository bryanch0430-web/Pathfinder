"""Tiny retrieval evaluation for the versioned soft-guide corpus.

Run: PYTHONPATH=. python -m app.guide_eval
"""

from __future__ import annotations

from app.application.soft_guides import retrieve

CASES = [
    # Hong Kong
    ("Hong Kong", "Lantau cable car Tai O fishing village", "hk-lantau"),
    ("Hong Kong", "Big Buddha Ngong Ping early morning", "hk-lantau"),
    ("Hong Kong", "Mid-Levels escalator PMQ Hollywood Road", "hk-central"),
    ("Hong Kong", "Sheung Wan ferry Central walking", "hk-central"),
    ("Hong Kong", "Temple Street Yau Ma Tei evening", "hk-kowloon"),
    ("Hong Kong", "Temple Street night market Kowloon", "hk-kowloon"),
    # Japan
    ("Tokyo", "Asakusa Ueno Yanaka east side", "japan-tokyo"),
    ("Tokyo", "Shibuya Harajuku Shinjuku west side", "japan-tokyo"),
    ("Tokyo", "station transfers subway corridor", "japan-tokyo"),
    ("Kyoto", "Kiyomizu-dera Gion early walking", "japan-kyoto"),
    ("Kyoto", "Arashiyama half day", "japan-kyoto"),
    ("Kyoto", "Nishiki Market food stalls", "japan-kyoto"),
    ("Japan", "JR pass shinkansen reservation", "japan-transport"),
    ("Japan", "luggage forwarding coin lockers", "japan-transport"),
    ("Japan", "IC card trains buses", "japan-transport"),
    ("Japan", "etiquette tipping cash", "japan-transport"),
    # USA
    ("New York", "subway uptown downtown museums", "usa-new-york"),
    ("New York", "Lower Manhattan SoHo West Village walking", "usa-new-york"),
    ("New York", "Met museum timed entry", "usa-new-york"),
    ("New York", "tipping restaurant tax", "usa-new-york"),
    ("San Francisco", "hills cable car walking", "usa-san-francisco"),
    ("San Francisco", "Alcatraz timed ferry", "usa-san-francisco"),
    ("San Francisco", "fog Golden Gate layers", "usa-san-francisco"),
    ("San Francisco", "Mission Hayes Valley food", "usa-san-francisco"),
    ("Los Angeles", "traffic clusters Hollywood Santa Monica", "usa-los-angeles"),
    ("Los Angeles", "beach morning afternoon", "usa-los-angeles"),
    ("Los Angeles", "Griffith Observatory sunset", "usa-los-angeles"),
    ("United States", "ESTA visa entry rules", "usa-practical"),
    ("United States", "tipping sales tax", "usa-practical"),
    ("United States", "national parks timed entry", "usa-practical"),
]


COUNTRY_FOR_CITY = {
    "hong kong": "hk",
    "tokyo": "jp",
    "kyoto": "jp",
    "japan": "jp",
    "new york": "us",
    "san francisco": "us",
    "los angeles": "us",
    "united states": "us",
}


def run() -> dict[str, float]:
    top1 = 0
    reciprocal = 0.0
    noise = 0
    for destination, query, expected in CASES:
        results = retrieve(destination, query, limit=3)
        ids = [item["id"] for item in results]
        if ids and ids[0] == expected:
            top1 += 1
        if expected in ids:
            reciprocal += 1.0 / (ids.index(expected) + 1)
        country = COUNTRY_FOR_CITY.get(destination.lower())
        if country:
            noise += sum(1 for item in results if item["country_code"].lower() != country)
    count = len(CASES)
    return {
        "cases": count,
        "top1": round(top1 / count, 4),
        "mrr": round(reciprocal / count, 4),
        "cross_destination_noise": noise,
    }


if __name__ == "__main__":
    print(run())
