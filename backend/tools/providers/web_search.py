"""Web / travel-notes search provider — PROVISIONAL (team decision 5: vendors are provisional).

The proposal's diagram has the attraction agent reading travel notes/pages. Results are
untrusted text: callers must fence them as data (prompt-injection defence).
"""

from __future__ import annotations

from backend.schemas.tools import SearchHit, WebSearchRequest


class WebSearchProvider:
    name = "web-search"

    async def search(self, request: WebSearchRequest) -> list[SearchHit]:
        # TODO(provisional): notes/pages search vendor not chosen (e.g. Xiaohongshu notes search
        # per the proposal diagram, or a general web search API), followed by note cleaning.
        raise NotImplementedError("TODO(provisional): web search is not implemented")
