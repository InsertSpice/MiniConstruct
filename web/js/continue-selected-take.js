// One cohesive saved mutation; navigation tickets also reject Project ABA races.
export function createTakeContinuation({ api, getContext, navigationAuthority, confirm, ready, adopt, notify }) {
  let pending = false;
  return async function continueSelectedTake(take, observation, hasLocalEdits = () => false) {
    if (pending || !ready()) return;
    if (!take) { notify("Select a Take first before continuing."); return; }
    const c = { ...getContext() };
    let ticket = navigationAuthority.capture(c.projectId, c.generationId);
    const owns = () => {
      const now = getContext();
      return navigationAuthority.ownsContext(ticket, now.projectId, now.generationId) && !now.replacing;
    };
    const unchanged = () => owns() && !getContext().dirty && !hasLocalEdits()
      && getContext().projectFingerprint === c.projectFingerprint;
    const index = c.generations.findIndex(g => g.generationId === c.generationId);
    if (index < 0) { notify("Reload the saved Generation before continuing."); return; }
    pending = true;
    try {
      const later = index < c.generations.length - 1;
      if (!await confirm(`Continue from Selected Take “${take.originalFilename}”? Create a new Generation immediately after this one.${later ? " Inserting here can affect later Generations’ continuity dependencies." : ""} ${observation ? "Its saved observation will supply observed continuity." : "No observation is available. Analyze Selected Take first for stronger observed continuity, or continue with planned context and visual handoff."}`)) return;
      if (!unchanged()) return;
      ticket = navigationAuthority.begin(c.projectId, c.generationId);
      const project = await api.continueSelectedTake(c.projectId, c.generationId, {
        expectedProjectFingerprint: c.projectFingerprint, takeId: take.id, takeSha256: take.sha256,
      });
      if (!owns()) return;
      if (!unchanged()) {
        notify("Continuation Generation created in the saved Project. Your current edits were kept; reload when ready to open it.");
        return;
      }
      adopt(project);
      notify(`Continuation Generation created · Continue previous shot · Source: ${take.originalFilename} · ${observation ? "Saved observation available" : "No saved observation"}. Describe the next action, then Generate the H3 prompt, Prepare and Send explicitly. Check Visual Handoff / Workflow Profile compatibility before sending.`);
    } catch (error) {
      if (owns()) notify(`Could not create continuation: ${error.message}`);
    } finally { pending = false; }
  };
}
