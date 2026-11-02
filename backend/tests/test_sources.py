from app.infrastructure.providers.places import _sections, _templates


def test_wikivoyage_nested_sections_keep_listings() -> None:
    wikitext = """== See ==
Intro text.
=== World Heritage Sites ===
{{see
| name = Kiyomizu-dera
| lat = 34.9949
| long = 135.785
| content = Hilltop temple.
}}
== Eat ==
=== Restaurants ===
{{eat|name=Test Restaurant|lat=35.0|long=135.7}}
"""
    sections = _sections(wikitext)
    assert "kiyomizu" in sections["see"].lower()
    assert "test restaurant" in sections["eat"].lower()
    templates = _templates(sections["see"])
    assert any(params.get("name") == "Kiyomizu-dera" for _, params in templates)
