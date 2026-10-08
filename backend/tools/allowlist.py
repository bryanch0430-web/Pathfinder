"""Per-path (and per-agent) tool allowlist.

Proposal: "each path may call only the tools on its allowlist". A call is allowed only if the
tool is in the PATH set and, when an agent is named, also in that AGENT's set. The intersection
means an agent can never widen what its path allows (e.g. the weather agent on the plan path may
call only `weather`, even though the plan path itself allows every tool).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from backend.schemas.common import AgentName, PathName, ToolName

PATH_ALLOWLIST: Mapping[PathName, frozenset[ToolName]] = {
    PathName.ROUTER: frozenset(),
    PathName.CLARIFY: frozenset(),
    PathName.PLAN: frozenset(ToolName),
    PathName.MODIFY: frozenset(ToolName),
    PathName.ASK: frozenset({ToolName.WEATHER, ToolName.PLACES, ToolName.MAPS, ToolName.TICKETS}),
}

AGENT_ALLOWLIST: Mapping[AgentName, frozenset[ToolName]] = {
    AgentName.ATTRACTION: frozenset({ToolName.WEB_SEARCH, ToolName.PLACES, ToolName.MAPS}),
    AgentName.HOTEL: frozenset({ToolName.PLACES, ToolName.MAPS}),
    AgentName.WEATHER: frozenset({ToolName.WEATHER}),
    AgentName.TICKET: frozenset({ToolName.TICKETS}),
}


class ToolAllowlist:
    def __init__(
        self,
        path_tools: Mapping[PathName, frozenset[ToolName]] = PATH_ALLOWLIST,
        agent_tools: Mapping[AgentName, frozenset[ToolName]] = AGENT_ALLOWLIST,
    ) -> None:
        # Copy into read-only views so a caller mutating its dict later cannot widen the policy.
        self._path_tools: Mapping[PathName, frozenset[ToolName]] = MappingProxyType(
            {path: frozenset(tools) for path, tools in path_tools.items()}
        )
        self._agent_tools: Mapping[AgentName, frozenset[ToolName]] = MappingProxyType(
            {agent: frozenset(tools) for agent, tools in agent_tools.items()}
        )

    def allowed_tools(self, path: PathName, agent: AgentName | None) -> frozenset[ToolName]:
        """Tools callable on `path` (intersected with `agent`'s set when an agent is named).

        Unknown paths/agents get the empty set: deny by default."""
        path_set = self._path_tools.get(path, frozenset())
        if agent is None:
            return path_set
        return path_set & self._agent_tools.get(agent, frozenset())

    def is_allowed(self, path: PathName, agent: AgentName | None, tool: ToolName) -> bool:
        return tool in self.allowed_tools(path, agent)
