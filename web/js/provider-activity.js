// Page-local coordination for prompt-writing requests. Tokens are object identities.
export function createProviderActivity({ now = () => Date.now(), setTimer = setInterval, clearTimer = clearInterval } = {}) {
  let current = null, timer = null;
  const listeners = new Set();
  const notify = () => { for (const listener of listeners) listener(current); };
  const conflict = (id, label) => !current ? null : current.id === id
    ? `${current.requestLabel || current.label} is already running.`
    : `Cannot start ${label} while ${current.requestLabel || current.label} is still running. Cancel or finish the current generation first.`;
  return {
    active: () => current,
    conflict,
    begin({ id, label, owner, cancel, verb = "Generating", requestLabel = label }) {
      if (current) return null;
      const token = { id, label, owner, cancel, verb, requestLabel, startedAt: now(), cancelling: false };
      current = token;
      timer = setTimer(notify, 1000);
      notify();
      return token;
    },
    finish(token) {
      if (current !== token) return false;
      current = null;
      if (timer != null) clearTimer(timer);
      timer = null;
      notify();
      return true;
    },
    cancelActive() {
      if (!current || current.cancelling) return;
      current.cancelling = true;
      notify();
      current.cancel();
    },
    subscribe(listener) { listeners.add(listener); listener(current); return () => listeners.delete(listener); },
    elapsed(token) { return Math.max(0, Math.floor((now() - token.startedAt) / 1000)); },
  };
}

export function formatActivityElapsed(seconds) {
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

export function beginProviderActivity(activity, details, onConflict) {
  const message = activity.conflict(details.id, details.label);
  if (message) { onConflict?.(message); return null; }
  return activity.begin(details);
}

export function mountProviderActivity(root, activity) {
  const label = root.querySelector("#provider-activity-label");
  const cancel = root.querySelector("#provider-activity-cancel");
  cancel.addEventListener("click", () => activity.cancelActive());
  return activity.subscribe(active => {
    root.hidden = !active;
    if (!active) return;
    label.textContent = `${active.cancelling ? "Cancelling" : active.verb} — ${active.label} · ${formatActivityElapsed(activity.elapsed(active))}`;
    cancel.textContent = active.id.startsWith("h3-") ? "Stop" : "Cancel";
  });
}
