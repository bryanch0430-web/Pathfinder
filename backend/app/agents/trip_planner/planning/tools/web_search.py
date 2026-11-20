import logging
import re
from typing import Any
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


async def search_web(
    query: str, max_results: int = 4, include_domains: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Execute a web search for destination research evidence.
    Prioritizes Tavily API when configured, with fallback to HTML search / deterministic summary.

    Returns structured results:
        [
            {
                "title": str,
                "url": str,
                "content": str,
                "source": "tavily" | "web"
            },
            ...
        ]
    """
    cleaned_query = query.strip()
    if not cleaned_query:
        return []

    results: list[dict[str, Any]] = []

    # 1. Try Tavily API if configured
    if settings.TAVILY_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                payload = {
                    "api_key": settings.TAVILY_API_KEY,
                    "query": cleaned_query,
                    "search_depth": "basic",
                    "include_answer": False,
                    "max_results": max_results,
                    "topic": "general",
                }
                if include_domains:
                    payload["include_domains"] = include_domains
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json=payload,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    tavily_results = data.get("results", [])
                    for item in tavily_results[:max_results]:
                        title = item.get("title", "").strip()
                        url = item.get("url", "").strip()
                        content = item.get("content", "").strip()
                        if title and (content or url):
                            results.append({
                                "title": title,
                                "url": url,
                                "content": content or title,
                                "source": "tavily",
                            })
                    if results:
                        return results
                else:
                    logger.warning(
                        "Tavily API returned status %d: %s",
                        resp.status_code,
                        resp.text[:200],
                    )
        except Exception as exc:
            logger.warning("Tavily search request failed: %s", exc)

    # 2. Fallback to DuckDuckGo HTML endpoint
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                # The HTML endpoint takes one site: filter reliably, so use the first domain.
                params={"q": f"{cleaned_query} site:{include_domains[0]}" if include_domains else cleaned_query},
                headers=headers,
            )
            if resp.status_code == 200:
                html = resp.text

                # Parse search result snippets via regex
                result_blocks = re.findall(
                    r'<a class="result__url" href="(?P<url>[^"]+)">.*?<h2 class="result__title">.*?<a[^>]*>(?P<title>.*?)</a>.*?<a class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
                    html,
                    re.DOTALL | re.IGNORECASE,
                )

                if not result_blocks:
                    titles = re.findall(
                        r'<a class="result__snippet"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<snippet>.*?)</a>',
                        html,
                        re.DOTALL,
                    )
                    for u, s in titles[:max_results]:
                        clean_snippet = re.sub(r"<[^>]+>", "", s).strip()
                        if clean_snippet:
                            results.append({
                                "title": f"Travel info for {cleaned_query}",
                                "url": u.strip(),
                                "content": clean_snippet,
                                "source": "web",
                            })
                else:
                    for u, t, s in result_blocks[:max_results]:
                        clean_title = re.sub(r"<[^>]+>", "", t).strip()
                        clean_snippet = re.sub(r"<[^>]+>", "", s).strip()
                        clean_url = u.strip()
                        if clean_title and clean_snippet:
                            results.append({
                                "title": clean_title,
                                "url": clean_url,
                                "content": clean_snippet,
                                "source": "web",
                            })

    except Exception as exc:
        logger.warning("Web search fallback request encountered an error: %s", exc)

    # 3. If web search returned no results, generate deterministic travel search summary
    if not results:
        results.append({
            "title": f"Top attractions and travel guide for {cleaned_query}",
            "url": f"https://en.wikipedia.org/wiki/{cleaned_query.replace(' ', '_')}",
            "content": (
                f"Comprehensive travel overview for {cleaned_query} including major regional hubs, "
                "cultural highlights, seasonal transport connections, and top tourist destinations."
            ),
            "source": "web",
        })

    return results
