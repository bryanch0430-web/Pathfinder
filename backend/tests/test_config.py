def test_cors_origins_forgive_trailing_slashes_quotes_and_spaces():
    """Seen in production: CORS_ORIGINS with a trailing slash blocked every request from the Vercel site."""
    from app.core.config import Settings

    parse = Settings.parse_cors_origins
    assert parse(" https://pathfinder-0.vercel.app/ ") == ["https://pathfinder-0.vercel.app"]
    assert parse('"https://a.vercel.app/", http://localhost:5173') == ["https://a.vercel.app", "http://localhost:5173"]
    assert parse('["https://a.vercel.app/"]') == ["https://a.vercel.app"]
    assert parse(["http://localhost:5173/"]) == ["http://localhost:5173"]
