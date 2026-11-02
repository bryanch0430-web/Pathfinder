from app.application.soft_guides import retrieve


def test_retrieval_is_destination_filtered_and_cited() -> None:
    items = retrieve("Hong Kong", "Lantau early morning cable car")
    assert items
    assert items[0]["destination"] == "hong kong"
    assert items[0]["url"].startswith("https://")
    assert all(item["destination"] == "hong kong" for item in items)


def test_retrieval_prefers_the_matching_section() -> None:
    items = retrieve("Kyoto", "Nishiki market food")
    assert items[0]["id"] == "japan-kyoto"
    assert items[0]["last_verified"] == "2026-10-30"
    assert items[0]["stale"] is False


def test_unknown_destination_returns_no_context() -> None:
    assert retrieve("Atlantis", "anything") == []


def test_corpus_covers_hong_kong_japan_and_usa() -> None:
    assert retrieve("Hong Kong", "Lantau")
    assert retrieve("Tokyo", "transit")
    assert retrieve("Kyoto", "temple")
    assert retrieve("Japan", "rail pass")
    assert retrieve("New York", "subway")
    assert retrieve("San Francisco", "fog")
    assert retrieve("Los Angeles", "traffic")
    assert retrieve("United States", "tipping")


def test_every_result_has_citation_and_version_metadata() -> None:
    for item in retrieve("Hong Kong", "Lantau"):
        assert item["url"].startswith("http")
        assert item["license"]
        assert item["content_hash"]
        assert item["corpus_version"]
