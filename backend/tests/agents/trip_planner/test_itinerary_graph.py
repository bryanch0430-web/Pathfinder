from app.agents.trip_planner.nodes.extract_itinerary_node import build_itinerary_graph


def test_graph_follows_generated_text_and_rejects_invented_stops():
    markdown = "## Days 1-3: Tokyo\nExplore Tokyo.\n## Days 4-6: Kyoto\nExplore Kyoto."
    graph = build_itinerary_graph(
        markdown, "Kolkata", "Japan", 6,
        candidates=[{"name": "Tokyo"}, {"name": "Kyoto"}],
        extracted={"stops": [
            {"name": "Tokyo", "day_start": 1, "day_end": 3},
            {"name": "Osaka", "day_start": 4, "day_end": 6},
            {"name": "Kyoto", "day_start": 4, "day_end": 6},
        ]},
    )
    assert [node["name"] for node in graph["nodes"]] == ["Kolkata", "Tokyo", "Kyoto"]
    assert graph["nodes"][2]["day_start"] == 4
    assert [(edge["source"], edge["target"]) for edge in graph["edges"]] == [
        ("origin", "stop-1"), ("stop-1", "stop-2"),
    ]


def test_graph_fallback_uses_candidate_order_in_text():
    graph = build_itinerary_graph(
        "Days 1-2: Kyoto, then Days 3-4: Tokyo.", "Delhi", "Japan", 4,
        candidates=[{"name": "Tokyo"}, {"name": "Kyoto"}],
    )
    assert [node["name"] for node in graph["nodes"]] == ["Delhi", "Kyoto", "Tokyo"]
    assert graph["nodes"][1]["day_start"] == 1


def test_explicit_return_route_reuses_existing_nodes():
    graph = build_itinerary_graph(
        "Route: Delhi ✈ Tokyo → Kyoto → Osaka → Delhi\n| 7 | Osaka → Tokyo → Delhi | Flight home |",
        "Delhi, India", "Japan", 7,
        extracted={"stops": [
            {"name": "Tokyo"}, {"name": "Kyoto"}, {"name": "Osaka"},
        ]},
    )
    assert [(edge["source"], edge["target"]) for edge in graph["edges"]] == [
        ("origin", "stop-1"), ("stop-1", "stop-2"), ("stop-2", "stop-3"),
        ("stop-3", "stop-1"), ("stop-1", "origin"),
    ]


def test_duration_with_unicode_spacing_is_preserved_only_when_in_text():
    graph = build_itinerary_graph(
        "Tokyo → Kyoto: Shinkansen ~2\u202fh\u202f40\u202fm.", "Delhi", "Japan", 3,
        extracted={"stops": [{"name": "Tokyo"}, {"name": "Kyoto"}],
                   "legs": [{"from": "Tokyo", "to": "Kyoto", "duration": "~2 h 40 m", "cost": "$200"}]},
    )
    assert graph["edges"][1]["duration"] == "~2 h 40 m"
    assert graph["edges"][1]["cost"] is None


def test_nearby_places_and_distance_require_itinerary_evidence():
    markdown = "| 1 | Tokyo | Visit Meiji Shrine and eat at Ichiran Ramen |\nTokyo → Kyoto: 450 km by rail."
    graph = build_itinerary_graph(
        markdown, "Delhi", "Japan", 3,
        extracted={"stops": [
            {"name": "Tokyo", "nearby_places": [
                {"name": "Meiji Shrine", "category": "attraction"},
                {"name": "Ichiran Ramen", "category": "restaurant"},
                {"name": "Made Up Cafe", "category": "restaurant"},
            ]},
            {"name": "Kyoto"},
        ], "legs": [{"from": "Tokyo", "to": "Kyoto", "distance": "450 km"}]},
    )
    assert [place["name"] for place in graph["nodes"][1]["nearby_places"]] == ["Meiji Shrine", "Ichiran Ramen"]
    assert graph["edges"][1]["distance"] == "450 km"


def test_highlights_and_optional_alternatives_are_not_route_stops():
    markdown = """| DAY | BASE / MAIN MOVE | HIGHLIGHTS |
| --- | --- | --- |
| Day 1 | Mumbai → Pune → Goa | Shaniwar Wada and FC Road |
| Day 2 | Panaji | Visit Old Goa and Anjuna Beach |
| Day 3 | Panaji | Baga or Calangute beaches |
"""
    graph = build_itinerary_graph(
        markdown, "Mumbai", "Goa", 3,
        extracted={"stops": [
            {"name": "Pune"}, {"name": "Panaji", "nearby_places": [
                {"name": "Old Goa", "category": "attraction"},
                {"name": "Anjuna Beach", "category": "attraction"},
            ]},
            {"name": "Old Goa"}, {"name": "Anjuna Beach"},
            {"name": "Baga"}, {"name": "Calangute"},
        ]},
    )
    assert [node["name"] for node in graph["nodes"]] == ["Mumbai", "Pune", "Panaji"]
    assert [place["name"] for place in graph["nodes"][2]["nearby_places"]] == ["Old Goa", "Anjuna Beach"]


def test_route_order_can_revisit_a_hub_without_duplicate_nodes():
    markdown = """| DAY | BASE | HIGHLIGHTS |
| --- | --- | --- |
| Day 1 | Mumbai → Pune → Dabolim | Transfer to Panaji |
| Day 2 | Panaji | Explore the city |
| Day 4 | Panaji → Dabolim Airport → Mumbai | Fly home |
"""
    graph = build_itinerary_graph(markdown, "Mumbai, India", "Goa", 4,
                                  extracted={"stops": [{"name": "Pune"}, {"name": "Dabolim Airport"},
                                                       {"name": "Panaji"}],
                                             "route_order": ["Mumbai", "Pune", "Dabolim", "Panaji",
                                                             "Dabolim Airport", "Mumbai"]})
    assert [node["name"] for node in graph["nodes"]] == ["Mumbai, India", "Pune", "Dabolim Airport", "Panaji"]
    assert [(edge["source"], edge["target"]) for edge in graph["edges"]] == [
        ("origin", "stop-1"), ("stop-1", "stop-2"), ("stop-2", "stop-3"),
        ("stop-3", "stop-2"), ("stop-2", "origin"),
    ]


def test_logistics_table_supplies_mode_and_time_for_matching_leg():
    markdown = """| 1 | Mumbai → Pune → Dabolim | Arrive |
| TRAVEL | OPTIONS | TIME |
| Pune → Goa (Dabolim) | Direct flight | 1 h |
"""
    graph = build_itinerary_graph(markdown, "Mumbai", "Goa", 1,
                                  extracted={"stops": [{"name": "Pune"}, {"name": "Dabolim"}]})
    assert graph["edges"][1]["mode"] == "Direct flight"
    assert graph["edges"][1]["duration"] == "1 h"


def test_day_table_keeps_airport_transfer_and_departure_loop():
    markdown = """| DAY | BASE / MAIN MOVE | HIGHLIGHTS |
| --- | --- | --- |
| Day 1 | Mumbai → Pune → Goa (evening) | Fly from Pune to Dabolim Airport, then transfer to Panaji. |
| Day 2 | Panaji | Explore Fontainhas. |
| Day 4 | Panaji & departure | Head to Dabolim Airport for your flight back to Mumbai. |
| TRAVEL | OPTIONS | TIME |
| Pune → Goa (Dabolim) | Direct flight | 1 h |
"""
    graph = build_itinerary_graph(
        markdown, "Mumbai, India", "Goa", 4,
        extracted={"stops": [{"name": "Pune"}, {"name": "Panaji"}],
                   "route_order": ["Mumbai", "Pune", "Panaji", "Mumbai"]},
    )
    assert [node["name"] for node in graph["nodes"]] == [
        "Mumbai, India", "Pune", "Dabolim Airport", "Panaji",
    ]
    assert [(edge["source"], edge["target"]) for edge in graph["edges"]] == [
        ("origin", "stop-1"), ("stop-1", "stop-3"), ("stop-3", "stop-2"),
        ("stop-2", "stop-3"), ("stop-3", "origin"),
    ]
    assert graph["edges"][1]["mode"] == "Direct flight"
    assert graph["edges"][3]["mode"] is None


def test_bold_day_rows_do_not_repeat_places_mentioned_in_explanations():
    markdown = """| **Day 1** | **Mumbai → Pune → Goa** | From Mumbai to Pune. Fly from Pune to Dabolim, then transfer to **Panaji**. |
| **Day 4** | **Panaji & departure** | Head to Dabolim Airport for your flight back to Mumbai. |
"""
    graph = build_itinerary_graph(markdown, "Mumbai, India", "Goa", 4,
                                  extracted={"stops": [{"name": "Pune"}, {"name": "Panaji"}]})
    assert [node["name"] for node in graph["nodes"]] == [
        "Mumbai, India", "Pune", "Dabolim Airport", "Panaji",
    ]
    assert [(edge["source"], edge["target"]) for edge in graph["edges"]] == [
        ("origin", "stop-1"), ("stop-1", "stop-3"), ("stop-3", "stop-2"),
        ("stop-2", "stop-3"), ("stop-3", "origin"),
    ]


def test_day_table_recovers_named_highlights_when_extraction_is_unavailable():
    markdown = """| **Day 1** | **Mumbai → Pune** | **Morning:** See **FC Road** before going to **Panaji**. |
| **Day 2** | **Panaji** | Visit **Latin Quarter (Fontainhas)** and **Museum of Christian Art**. |
| **Day 4** | **Panaji & departure** | Return to Mumbai. |
"""
    graph = build_itinerary_graph(markdown, "Mumbai", "Goa", 4,
                                  requested_places=["Pune", "Panaji"])
    pune = next(node for node in graph["nodes"] if node["name"] == "Pune")
    panaji = next(node for node in graph["nodes"] if node["name"] == "Panaji")
    assert pune["day_start"] == 1
    assert [place["name"] for place in pune["nearby_places"]] == ["FC Road"]
    assert panaji["day_start"] == 1 and panaji["day_end"] == 4
    assert [place["name"] for place in panaji["nearby_places"]] == [
        "Latin Quarter (Fontainhas)", "Museum of Christian Art",
    ]
