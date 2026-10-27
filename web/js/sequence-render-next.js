// One explicit Sequence inspection. The existing execution panel owns Prepare and Send.
export function createSequenceRenderAction({ button, status, api, getProjectId, openAndPrepare }) {
  let epoch = 0;
  button.addEventListener("click", async () => {
    const ticket = ++epoch;
    const projectId = getProjectId();
    if (!projectId) {
      status.textContent = "Save a filesystem Project before inspecting Sequence readiness.";
      return;
    }
    button.disabled = true;
    status.textContent = "Inspecting saved Sequence…";
    try {
      const next = await api.sequenceRenderReadiness(projectId);
      if (ticket !== epoch || projectId !== getProjectId()) return;
      if (!next.candidate || !next.prepareReady) {
        status.textContent = next.reason;
        return;
      }
      const controls = (next.workflowControls || []).map(item => `${item.label}: ${item.value}`).join(" · ");
      status.textContent = `${next.reason} Prompt: ${next.promptSummary} · Profile: ${next.profileName} · `
        + `Continuity: ${next.continuitySource} · Renderer: ${next.rendererStatus} · Prepare: Ready. `
        + (controls ? `Controls: ${controls}. ` : "")
        + (next.connectionRequired ? "Choose a ComfyUI Connection before Send. " : `Connection: ${next.connectionName}. `)
        + "Send remains an explicit separate click.";
      await openAndPrepare(next);
    } catch (error) {
      if (ticket === epoch) status.textContent = `Sequence readiness failed: ${error.message}`;
    } finally {
      if (ticket === epoch) button.disabled = false;
    }
  });
  return { reset() { ++epoch; status.textContent = ""; button.disabled = false; } };
}
