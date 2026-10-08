/** The small grey status line under each assistant message ("Plan · 4 agents", ...). */
import type { AgentName, Route } from '@/api'

const AGENT_SHORT: Record<AgentName, string> = {
  attraction: 'attraction',
  hotel: 'hotel',
  weather: 'weather',
  ticket: 'ticket',
}

export function routeStatusLine(route: Route | null, agentsRun: readonly AgentName[] = []): string {
  switch (route) {
    case 'plan': {
      const n = agentsRun.length
      return n > 0 ? `Plan · ${n} ${n === 1 ? 'agent' : 'agents'}` : 'Plan'
    }
    case 'modify': {
      if (agentsRun.length === 0) return 'Modify'
      const names = agentsRun.map((agent) => AGENT_SHORT[agent]).join(' + ')
      return `Modify · ${names} ${agentsRun.length === 1 ? 'agent' : 'agents'}`
    }
    case 'ask':
      return 'Quick question'
    case 'unclear':
      return 'Needs clarification'
    default:
      return ''
  }
}
