"""Four English trip sets for the regions these travellers actually book.

1,000 queries each for Hong Kong, Taiwan, Japan and China. A quarter are tricky:
the thing they want to see is an animal, a flower, a food or a vibe, not the city.
The generator is seeded, so the files do not drift between runs.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "eval" / "regional"
SEED = 7608
COUNT = 1000

ORIGINS = [
    "Hong Kong", "Taipei", "Tokyo", "Osaka", "Seoul", "Singapore",
    "London", "Sydney", "Vancouver", "Bangkok", "Shanghai", "Beijing",
]

REGIONS = {
    "hongkong": {
        "cities": ["Hong Kong"],
        "wishes": [
            "dim sum", "neon signs", "Victoria Peak", "the Star Ferry",
            "a Dragon's Back hike", "egg waffles", "a dai pai dong",
            "the night skyline", "temple street", "milk tea",
        ],
    },
    "taiwan": {
        "cities": ["Taipei", "Kaohsiung", "Taichung", "Tainan", "Hualien"],
        "wishes": [
            "night markets", "beef noodle soup", "Jiufen", "a hot spring",
            "bubble tea", "the lantern festival", "taroko gorge",
            "sun cakes", "a quiet teahouse", "firefly season",
        ],
    },
    "japan": {
        "cities": ["Tokyo", "Kyoto", "Osaka", "Hiroshima", "Sapporo", "Fukuoka", "Nara", "Kanazawa"],
        "wishes": [
            "cherry blossoms", "sakura", "an onsen", "teamLab", "ramen",
            "the deer in Nara", "maple leaves", "a quiet temple",
            "Shinjuku at night", "sushi", "a fox shrine", "koi ponds",
        ],
    },
    "china": {
        "cities": ["Beijing", "Shanghai", "Chengdu", "Xi'an", "Hangzhou", "Guangzhou", "Guilin", "Chongqing"],
        "wishes": [
            "giant pandas", "hotpot", "the hutongs", "the terracotta army",
            "the Bund", "karst hills", "street food", "a red panda",
            "a tea house", "spicy noodles", "city lights", "a slow morning",
        ],
    },
}

SHAPES = [
    "Plan a {days}-day trip to {dest} from {origin} for {party} people, budget {budget} HKD.",
    "I'll be departing from {origin} and heading to {dest} for a {days}-day trip. There are {party} of us and the budget is {budget} HKD.",
    "We want {days} days in {dest}, leaving from {origin}, {party} people, about {budget} HKD.",
    "Please arrange a {days}-day trip for {party} people from {origin} to {dest}. Keep it within {budget} HKD.",
]

WISH_SHAPES = [
    "I want to see {wish} in {dest}. {days} days from {origin}, {party} people, budget {budget} HKD.",
    "I wish the trip is built around {wish}. We are flying from {origin} to {dest} for {days} days, {party} of us, {budget} HKD.",
    "The whole point is {wish}. Departing from {origin} and heading to {dest} for {days} days, party of {party}, {budget} HKD.",
    "Can you plan {days} days in {dest} from {origin} for {party} people, budget {budget} HKD, built around {wish}?",
]


def build(rng: random.Random, region: str, spec: dict) -> list[dict]:
    rows = []
    for index in range(COUNT):
        dest = rng.choice(spec["cities"])
        origin = rng.choice([city for city in ORIGINS if city.casefold() != dest.casefold()])
        days = rng.choice([3, 4, 5, 7])
        party = rng.choice([1, 2, 3, 4])
        budget = rng.choice([8000, 12000, 18000, 25000, 40000])
        tricky = index % 4 == 0
        wish = rng.choice(spec["wishes"]) if tricky else ""
        shape = rng.choice(WISH_SHAPES if tricky else SHAPES)
        text = shape.format(days=days, dest=dest, origin=origin, party=party, budget=budget, wish=wish)
        rows.append(
            {
                "id": f"{region}-{index:04d}",
                "region": region,
                "input": text,
                "origin": origin,
                "destination": dest,
                "days": days,
                "party_size": party,
                "budget": budget,
                "currency": "HKD",
                "wish": wish,
                "wish_is_not_a_city": bool(wish),
            }
        )
    return rows


def main() -> None:
    rng = random.Random(SEED)
    ROOT.mkdir(parents=True, exist_ok=True)
    for region, spec in REGIONS.items():
        rows = build(rng, region, spec)
        path = ROOT / f"{region}.jsonl"
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
        tricky = sum(1 for row in rows if row["wish"])
        print(f"{region}: {len(rows)} queries, {tricky} tricky")


if __name__ == "__main__":
    main()
