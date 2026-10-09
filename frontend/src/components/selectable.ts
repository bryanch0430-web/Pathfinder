/**
 * Shared behaviour of the selectable parts of the plan workspace (day, stop, hotel, ticket).
 * Each part is a role="button" element with aria-pressed; Escape on the workspace clears.
 */
import { useSessionStore } from '@/stores/session'

/** Enter or Space toggles the part, like a click. */
export function onSelectableKeydown(event: KeyboardEvent, toggle: () => void): void {
  if (event.key !== 'Enter' && event.key !== ' ') return
  if (event.target !== event.currentTarget) return
  event.preventDefault()
  toggle()
}

/**
 * Run a lock/unlock (the confirm endpoint) and return the server message to show next to the
 * control, or null. The confirm actions report into the global banner, so the message moves
 * inline unless the session itself was lost (the banner then offers a new session).
 */
export async function lockError(apply: () => Promise<boolean>): Promise<string | null> {
  const session = useSessionStore()
  if (await apply()) return null
  const message = session.lastError ?? 'The change was not saved.'
  if (!session.sessionLost) session.dismissError()
  return message
}
