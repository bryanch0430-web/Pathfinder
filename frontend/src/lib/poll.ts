/**
 * Runs `task` every `ms` while the page is visible. A tick is skipped while the previous call is still in flight
 * (single-flight), and polling pauses while the tab is hidden and catches up once when it is shown again.
 * Returns the function that stops it.
 */
export function startPolling(task: () => Promise<unknown> | void, ms: number): () => void {
  let busy = false;
  const tick = () => {
    if (busy || document.hidden) return;
    busy = true;
    Promise.resolve()
      .then(task)
      .catch(() => undefined)
      .finally(() => {
        busy = false;
      });
  };
  const timer = window.setInterval(tick, ms);
  const onVisible = () => {
    if (!document.hidden) tick();
  };
  document.addEventListener("visibilitychange", onVisible);
  return () => {
    window.clearInterval(timer);
    document.removeEventListener("visibilitychange", onVisible);
  };
}
