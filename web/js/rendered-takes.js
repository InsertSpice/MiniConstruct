import { renderObservedEndState } from "./observed-end-state.js";
import { formatLocalTimestamp } from "./local-time.js";
import { renderSubmissionLabels } from "./render-submission-label.js";
import { beginProviderActivity, createProviderActivity } from "./provider-activity.js";

// Actual outputs are Project-owned; UI selection is never inferred from chronology.
export function createRenderedTakes({ root, liveRoot = root, api, getContext: readContext, getSettings = () => ({}), onMutation: notifyMutation = () => {}, confirmDelete = message => confirm(message),
  confirmRetry = message => confirm(message),
  onContinue = null,
  onReconciled: notifyReconciled = () => {}, canChangeObservedState = () => true,
  providerActivity = createProviderActivity(),
  setTimer = (fn, ms) => setTimeout(fn, ms), clearTimer = id => clearTimeout(id), now = () => Date.now() }) {
  const doc = root.ownerDocument;
  let resetEpoch = 0;
  const getContext = () => ({ ...readContext(), generatedResetEpoch: resetEpoch });
  const onMutation = (response, c) => {
    const { generatedResetEpoch, ...context } = c;
    if (generatedResetEpoch === resetEpoch) notifyMutation(response, context);
  };
  const onReconciled = (response, c) => {
    const { generatedResetEpoch, ...context } = c;
    if (generatedResetEpoch === resetEpoch) notifyReconciled(response, context);
  };
  let key = "", epoch = 0, busy = false, state = null, selection = null, error = "", analysisError = "", previewId = null, inspectedTakeId = null;
  let timer = null, disposed = false, live = null, liveSocket = null, liveUrl = null, liveMime = null, liveClock = null;
  let liveMedia = null, liveMediaFamily = null;
  let foregroundProjectId = null, foregroundRows = [];
  let foregroundRead = 0;
  let discoveryCursor = 0;
  let foregroundUpdatedAt = null, foregroundUnavailable = false;
  let foregroundPending = null, historyTicket = null;
  const outputVersions = new Map();
  const outputVersion = generationId => outputVersions.get(generationId) || 0;
  const advanceOutputs = generationId => outputVersions.set(generationId, outputVersion(generationId) + 1);
  const cancellingIds = new Set();
  const retryingIds = new Set();
  const clearLive = () => {
    ++foregroundRead;
    if (liveClock !== null) { clearInterval(liveClock); liveClock = null; }
    if (liveSocket) { liveSocket.onmessage = liveSocket.onclose = liveSocket.onerror = null; liveSocket.close(); liveSocket = null; }
    if (liveUrl) { URL.revokeObjectURL(liveUrl); liveUrl = null; }
    liveMime = null; live = null; liveMedia = null; liveMediaFamily = null;
    if (liveRegion) { liveRegion.hidden = true; if (liveRoot !== root) liveRoot.hidden = true; liveMediaHost.replaceChildren(); liveLabel.textContent = ""; liveStatus.textContent = ""; liveNode.textContent = ""; liveProgress.hidden = true; liveProgressText.textContent = ""; }
  };
  const liveStatusText = session => {
    if (session.status === "disconnected")
      return "Live connection lost; automatic reconnect pending; renderer status is checked separately.";
    if (session.uncertain) return "Renderer connection unavailable; current job status is uncertain.";
    if (session.jobStatus === "unknown" || session.status === "unknown")
      return "Status unknown — active rendering is not confirmed. Recover checks the exact submission.";
    if (session.jobStatus === "queued") return "Queued — waiting for the renderer.";
    if (session.status === "reconciling") return "Checking exact renderer status…";
    const elapsed = Math.max(0, Math.floor((now() - session.runningSince) / 1000));
    return `Live: running · Elapsed ${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(elapsed % 60).padStart(2, "0")} observed running`;
  };
  const mediaFamily = mime => mime === "video/mp4" ? "video" : "image";
  function clearLiveMedia() {
    if (liveUrl) URL.revokeObjectURL(liveUrl);
    liveUrl = liveMime = liveMedia = liveMediaFamily = null;
    liveMediaHost.replaceChildren();
    if (live) { live.nodeId = null; live.value = live.max = null; live.previewMeta = null; }
  }
  function syncLiveClock() {
    const running = live && live.jobStatus === "running" && !live.uncertain
      && !["disconnected", "unknown", "reconciling"].includes(live.status);
    if (!running && liveClock !== null) { clearInterval(liveClock); liveClock = null; }
    if (running && liveClock === null) liveClock = setInterval(() => {
      if (live && sameLiveProject(getContext(), live) && mounted()) liveStatus.textContent = liveStatusText(live);
      else clearLive();
    }, 1000);
  }
  function updateLiveDom() {
    if (!live) return;
    liveRegion.hidden = false; if (liveRoot !== root) liveRoot.hidden = false;
    liveLabel.textContent = `Generation ${live.generationNumber} · ComfyUI send ${live.sendNumber}`;
    syncLiveClock();
    liveStatus.textContent = liveStatusText(live);
    liveCancel.textContent = cancellingIds.has(live.id) || live.cancelRequested ? "Cancel requested…" : "Cancel render";
    liveCancel.disabled = !api.cancelRenderSubmission || cancellingIds.has(live.id) || Boolean(live.cancelRequested)
      || !activeSubmission(watchedSubmission());
    liveNode.textContent = live.nodeId ? `Node ${live.nodeId}` : "";
    const hasProgress = Number.isFinite(live.value) && live.value >= 0
      && Number.isFinite(live.max) && live.max > 0;
    liveProgress.hidden = !hasProgress;
    liveProgressText.textContent = "";
    if (hasProgress) {
      const value = Math.min(live.value, live.max);
      const percentage = Math.max(0, Math.min(100, Math.round((live.value / live.max) * 100)));
      const label = live.nodeId ? live.nodeTypes?.[live.nodeId] || `Node ${live.nodeId}` : "Sampling";
      liveProgress.value = value;
      liveProgress.max = live.max;
      liveProgress.setAttribute("aria-label", `${label} progress`);
      liveProgressText.textContent = `${label} — ${live.value} / ${live.max} · ${percentage}%`;
    }
    if (!liveUrl || !liveMime) return;
    const family = mediaFamily(liveMime);
    if (!liveMedia || liveMediaFamily !== family) {
      liveMedia = doc.createElement(family === "video" ? "video" : "img");
      liveMediaFamily = family;
      if (family === "video") { liveMedia.autoplay = true; liveMedia.loop = true; liveMedia.muted = true; liveMedia.playsInline = true; }
      liveMediaHost.replaceChildren(liveMedia);
    }
    if (liveMedia.src !== liveUrl) liveMedia.src = liveUrl;
  }
  function watch(id, selectionMode, target = null, reconnect = false) {
    const c = { ...getContext() };
    const submission = target || visibleRows(c).find(s => s.id === id);
    if (!api.watchRenderSubmission || !activeSubmission(submission) || !pageVisible()) return;
    ++foregroundRead;
    const previous = reconnect ? live : null;
    if (reconnect) {
      if (liveSocket) { liveSocket.onmessage = liveSocket.onclose = liveSocket.onerror = null; liveSocket.close(); liveSocket = null; }
      if (liveClock !== null) { clearInterval(liveClock); liveClock = null; }
    } else clearLive();
    const session = { id, selectionMode, generatedResetEpoch: c.generatedResetEpoch, projectId: c.projectId, generationId: submission.generationId, promptId: submission.comfyPromptId,
      generationNumber: submission.generationNumber, sendNumber: submission.sendNumber,
      jobStatus: submission.job.status, runningSince: now(),
      uncertain: foregroundUnavailable, status: "connecting", nodeId: null, nodeTypes: {}, value: null, max: null,
      reconnectFailures: previous?.reconnectFailures || 0,
      cancelRequested: previous?.cancelRequested || false };
    live = session;
    const disconnected = () => {
      if (live !== session) return;
      if (session.status !== "disconnected") {
        session.reconnectFailures++;
        session.reconnectAt = now() + Math.min(120000, 15000 * 2 ** Math.min(session.reconnectFailures - 1, 3));
      }
      session.status = "disconnected";
      clearLiveMedia();
      // Retire the transport immediately. Late state/connected events from this
      // failed socket must not erase the recovery state while HTTP is in flight.
      if (liveSocket) {
        liveSocket.onmessage = liveSocket.onclose = liveSocket.onerror = null;
        liveSocket.close(); liveSocket = null;
      }
      render(); updateLiveDom(); schedulePolling();
    };
    let socket;
    try { socket = api.watchRenderSubmission(c.projectId, session.generationId, id); }
    catch { disconnected(); return; }
    liveSocket = socket;
    socket.binaryType = "arraybuffer";
    const current = () => live === session && liveSocket === socket && sameLiveProject(getContext(), session) && mounted();
    socket.onmessage = event => {
      if (!current()) return;
      if (typeof event.data !== "string") {
        if (session.jobStatus === "unknown" || session.uncertain || session.status === "unknown") return;
        if (!session.previewMeta || !["image/jpeg", "image/png", "image/webp", "video/mp4"].includes(session.previewMeta.mime)) return;
        const bytes = event.data;
        if ((bytes.byteLength ?? bytes.size ?? 0) > 12 * 1024 * 1024) return;
        const next = URL.createObjectURL(new Blob([bytes], { type: session.previewMeta.mime }));
        if (liveUrl) URL.revokeObjectURL(liveUrl);
        liveUrl = next; liveMime = session.previewMeta.mime; session.reconnectFailures = 0; updateLiveDom(); return;
      }
      let data;
      try { data = JSON.parse(event.data); } catch { return; }
      const eventPrompt = data.promptId ?? data.prompt_id;
      if (eventPrompt != null && (!session.promptId || String(eventPrompt) !== String(session.promptId))) return;
      if (data.type === "connected") {
        session.status = "connected";
        session.nodeTypes = data.nodeTypes && typeof data.nodeTypes === "object" && !Array.isArray(data.nodeTypes)
          ? data.nodeTypes : {};
      }
      if (data.type === "state") {
        session.status = data.status;
        if (data.status === "unknown") clearLiveMedia();
      }
      if (data.type === "executing" && session.jobStatus !== "unknown" && !session.uncertain && session.status !== "unknown") {
        session.nodeId = typeof data.nodeId === "string" && /^[A-Za-z0-9_.-]{1,80}$/.test(data.nodeId) ? data.nodeId : null;
        session.value = session.max = null;
      }
      if (data.type === "progress" && session.jobStatus !== "unknown" && !session.uncertain && session.status !== "unknown") {
        const nodeId = typeof data.nodeId === "string" && /^[A-Za-z0-9_.-]{1,80}$/.test(data.nodeId) ? data.nodeId : null;
        const value = data.value, max = data.max;
        if (Number.isFinite(value) && value >= 0 && Number.isFinite(max) && max > 0) {
          if (nodeId && nodeId !== session.nodeId) session.value = session.max = null;
          session.nodeId = nodeId || session.nodeId;
          session.value = value; session.max = max;
          session.reconnectFailures = 0;
        }
      }
      if (data.type === "preview-meta") session.previewMeta = data;
      if ((data.type === "terminal" || data.type === "reconcile") && session.status !== "reconciling") {
        session.status = "reconciling"; session.nodeId = null; session.value = session.max = null; updateLiveDom();
        void reconcileWatched(session).finally(() => {
          if (live === session) {
            // A socket terminal hint is not canonical terminal truth.
            if (liveSocket === socket && socket.readyState !== 2 && socket.readyState !== 3)
              session.status = watchedSubmission()?.job?.status || "connected";
            else disconnected();
            ensureLiveObservation(); render(); schedulePolling();
          }
        });
      }
      if (data.type === "disconnected") disconnected();
      updateLiveDom();
    };
    socket.onclose = () => { if (current()) disconnected(); };
    socket.onerror = () => { if (current()) disconnected(); };
    render(); updateLiveDom();
  }
  const sameLiveProject = (c, session) => (c.saved || c.replacing) && c.projectId === session.projectId
    && (session.generatedResetEpoch ?? resetEpoch) === resetEpoch;
  const activePriority = { running: 0, queued: 1, unknown: 2 };
  const confirmedSubmission = item => ["running", "queued"].includes(item?.job?.status);
  const activeSubmission = item => Boolean(item && Object.hasOwn(activePriority, item.job?.status));
  function visibleRows(c = getContext(), outputs = state) {
    const ordered = [...(outputs?.renderSubmissions || [])].sort((a, b) =>
      String(a.createdAt).localeCompare(String(b.createdAt)) || String(a.id).localeCompare(String(b.id)));
    const generationNumber = (c.generations || []).findIndex(g => g.generationId === c.generationId) + 1;
    return ordered.map((item, index) => ({ ...item, generationId: c.generationId,
      generationNumber: generationNumber || 1, sendNumber: index + 1 }));
  }
  function rememberOutputs(response, c) {
    if (!sameLiveProject(getContext(), c) || foregroundProjectId !== c.projectId) return;
    if (response.projectUpdatedAt && foregroundUpdatedAt
        && Date.parse(response.projectUpdatedAt) < Date.parse(foregroundUpdatedAt)) return;
    if (response.projectUpdatedAt) foregroundUpdatedAt = response.projectUpdatedAt;
    // Keep labels from the Project summary when the watched Generation is offscreen.
    const previous = foregroundRows.find(row => row.generationId === c.generationId);
    const rows = visibleRows(c, response).map(row => ({ ...row,
      generationNumber: previous?.generationNumber || row.generationNumber }));
    foregroundRows = [...foregroundRows.filter(row => row.generationId !== c.generationId), ...rows];
  }
  async function readForeground(c, discover = false) {
    if (!api.foregroundRenders) return;
    const request = ++foregroundRead;
    const watched = live && c.projectId === live.projectId ? { generationId: live.generationId, id: live.id } : null;
    const summary = await api.foregroundRenders(c.projectId, watched, discover ? { refresh: true, cursor: discoveryCursor } : undefined);
    if (!mounted() || request !== foregroundRead || !sameLiveProject(getContext(), c)) return;
    if (summary.projectUpdatedAt && foregroundUpdatedAt
        && Date.parse(summary.projectUpdatedAt) < Date.parse(foregroundUpdatedAt)) return;
    if (summary.projectUpdatedAt) foregroundUpdatedAt = summary.projectUpdatedAt;
    foregroundProjectId = c.projectId;
    foregroundUnavailable = Boolean(summary.discoveryError);
    foregroundRows = [summary.candidate, summary.watched].filter(Boolean);
    if (summary.projectFingerprint) onMutation(summary, c);
    if (Number.isInteger(summary.discoveryCursor)) discoveryCursor = summary.discoveryCursor;
    if (summary.discoveryError) error = summary.discoveryError;
  }
  const watchedSubmission = () => live && foregroundRows.find(row => row.id === live.id && row.generationId === live.generationId);
  function ensureLiveObservation() {
    const c = getContext();
    if (live && !sameLiveProject(c, live)) clearLive();
    if (!saved(c) || !pageVisible()) return;
    if (foregroundProjectId !== c.projectId) { foregroundProjectId = c.projectId; foregroundRows = []; }
    if (!api.foregroundRenders && state) rememberOutputs(state, c);
    // Fresh canonical priority/terminal truth can transfer observation while an
    // older watcher reconciliation or completed-output download is still pending.
    const watched = watchedSubmission();
    if (live?.status === "reconciling" && !watched) return;
    // Chronology is only an observation preference, never output authority.
    const candidates = foregroundRows.filter(confirmedSubmission).sort((a, b) =>
      activePriority[a.job.status] - activePriority[b.job.status]
      || a.generationNumber - b.generationNumber
      || (Date.parse(b.createdAt) || 0) - (Date.parse(a.createdAt) || 0)
      || (a.id < b.id ? 1 : a.id > b.id ? -1 : 0));
    if (watched && activeSubmission(watched) && (confirmedSubmission(watched) || live.selectionMode === "explicit")) {
      const confirmedAgain = live.status === "unknown" && watched.job.status !== "unknown";
      if (live.jobStatus !== watched.job.status || live.uncertain !== foregroundUnavailable || confirmedAgain) {
        live.runningSince = now();
        if (watched.job.status !== "running" || foregroundUnavailable) clearLiveMedia();
      }
      if ((live.jobStatus !== watched.job.status || confirmedAgain) && !["disconnected", "reconciling"].includes(live.status)) live.status = "connecting";
      live.jobStatus = watched.job.status; live.uncertain = foregroundUnavailable;
      live.generationNumber = watched.generationNumber; live.sendNumber = watched.sendNumber;
      // Explicit choices remain active even when another job has better priority.
      // Automatic choices upgrade only by canonical job status, never socket state
      // or chronology within the same priority class (including on disconnect).
      if (live.selectionMode === "explicit"
          || !candidates.length || activePriority[candidates[0].job.status] >= activePriority[watched.job.status]) {
        if (live.status === "disconnected" && now() >= live.reconnectAt)
          watch(live.id, live.selectionMode, watched, true);
        return;
      }
    }
    if (live) clearLive();
    if (candidates.length) watch(candidates[0].id, "automatic", candidates[0]);
  }
  async function reconcileWatched(session) {
    // The watched job can outlive navigation to another Generation. Capture
    // authority per HTTP request, rather than reusing the socket's old context.
    const context = { ...getContext(), generationId: session.generationId };
    if (!sameLiveProject(context, session)) return;
    const version = outputVersion(session.generationId);
    try {
      const response = api.reconcileRenderSubmission
        ? await api.reconcileRenderSubmission(session.projectId, session.generationId, session.id)
        : await api.generationOutputs(session.projectId, session.generationId);
      if (disposed || live !== session || !sameLiveProject(getContext(), session)
          || version !== outputVersion(session.generationId)) return;
      if (api.reconcileRenderSubmission) onMutation(response, context);
      if (!disposed && live === session && sameLiveProject(getContext(), session)) {
        advanceOutputs(session.generationId);
        rememberOutputs(response, session);
        if (getContext().generationId === session.generationId) {
          state = response; error = response.reconciliationError || ""; options();
        }
        await readForeground(context, true);
      }
      if (api.reconcileRenderSubmission) onReconciled(response, session);
    } catch { /* The ordinary exact status controls can recover a failed reconciliation. */ }
  }
  async function watchSubmitted(response, c) {
    c = { ...c, generatedResetEpoch: c.generatedResetEpoch ?? resetEpoch };
    if (c.generatedResetEpoch !== resetEpoch) return;
    if (response.renderSubmissionId && contextKey(c) === contextKey(getContext())) {
      try {
        const outputs = await api.generationOutputs(c.projectId, c.generationId);
        if (contextKey(c) === contextKey(getContext())) { onMutation(outputs, c); state = outputs; options(); }
      } catch { /* Normal refresh reports read errors. */ }
    }
    await refresh();
    ensureLiveObservation(); render();
  }
  const mounted = () => !disposed && root.isConnected !== false;
  const pageVisible = () => mounted() && !root.hidden && doc.visibilityState !== "hidden";
  const stopPolling = () => { if (timer !== null) clearTimer(timer); timer = null; };
  function schedulePolling() {
    stopPolling();
    if (!pageVisible() || !saved(getContext()) || !api.reconcileRenderSubmission || (busy && historyTicket !== epoch)
        || (!api.foregroundRenders && !live
          && !state?.renderSubmissions.some(s => !["completed", "failed", "cancelled"].includes(s.job?.status)))) return;
    timer = setTimer(() => { timer = null; if (pageVisible()) return refresh({ pendingOnly: true }); }, 15000);
  }
  const observationDraft = { key: "", value: null, dirty: false, editError: "" };
  const el = (tag, text, parent = root) => {
    const node = doc.createElement(tag); if (text != null) node.textContent = text; parent.append(node); return node;
  };
  const button = (id, text, action, parent = root) => {
    const node = el("button", text, parent); node.id = `takes-${id}`; node.type = "button";
    node.addEventListener("click", action); return node;
  };
  const saved = c => c.saved && c.projectId && c.generationId;
  const contextKey = c => JSON.stringify([c.saved, c.projectId, c.generationId, c.generatedResetEpoch ?? resetEpoch, c.navigationTicket?.id]);
  el("h3", "Rendered Takes");
  el("p", "Takes are actual rendered videos. Selected Take is the output you explicitly choose as this Generation’s visual result for visual continuity.").className = "helper";
  el("p", "Selecting a Take does not analyze its contents. Its existing observation can change later H3 continuity; Planned Continuity and Story Memory stay author-owned.").className = "helper";
  const status = el("p", ""); status.id = "takes-status"; status.setAttribute("role", "status");
  const refreshButton = button("refresh", "Refresh render status", () => refresh());
  const clear = button("clear", "Clear Selected Take", () => actObserved(c => api.clearSelectedTake(c.projectId, c.generationId)));
  let continuing = false;
  const continueTake = button("continue", "Continue from Selected Take", async () => {
    if (busy || continuing || !onContinue) return;
    if (observationDraft.dirty) { error = "Save or reset your Observed End State edits before continuing."; render(); return; }
    const take = state?.takes.find(t => t.id === state.selectedTakeId);
    const observation = state?.takeObservations?.find(o => o.takeId === take?.id && o.takeSha256 === take?.sha256);
    continuing = true; render();
    try { await onContinue(take, observation, () => observationDraft.dirty); }
    finally { continuing = false; render(); }
  });
  const takeSelectorRegion = el("div"); takeSelectorRegion.className = "take-selector";
  const takeLabel = el("label", "Inspect Take", takeSelectorRegion); takeLabel.setAttribute("for", "takes-inspected");
  const takeSelector = el("select", null, takeSelectorRegion); takeSelector.id = "takes-inspected";
  const list = el("div"); list.className = "take-list";
  const preview = el("div"); preview.className = "take-preview";
  const observationRoot = el("section"); observationRoot.className = "observed-end-state";
  const manual = el("details"); manual.id = "takes-manual-import"; manual.open = false;
  el("summary", "Manual import / fallback", manual);
  el("p", "Use this when the rendered video was not automatically imported by MiniConstruct.", manual).className = "helper";
  const form = el("fieldset", null, manual); el("legend", "Import video manually", form);
  const provenanceLabel = el("label", "This output came from", form);
  const source = el("select", null, provenanceLabel); source.id = "takes-source";
  const submissionLabel = el("label", "Recorded ComfyUI submission", form);
  const submission = el("select", null, submissionLabel); submission.id = "takes-submission";
  const sourceNote = el("p", "", form); sourceNote.className = "helper";
  const fileLabel = el("label", "Rendered video file", form);
  const file = el("input", null, fileLabel); file.id = "takes-file"; file.type = "file";
  file.accept = ".mp4,.webm,.mov,.mkv,.avi,.m4v,.mpeg,.mpg";
  const add = button("import", "Import Take", importCurrent, form);
  const liveRegion = el("section", null, liveRoot); liveRegion.className = "render-live-status"; liveRegion.hidden = true;
  if (liveRoot !== root) liveRoot.hidden = true;
  el("h3", "Live Render Preview", liveRegion);
  const liveLabel = el("p", "", liveRegion);
  const liveStatus = el("p", "", liveRegion); liveStatus.setAttribute("role", "status");
  const liveNode = el("p", "", liveRegion);
  const liveProgress = el("progress", null, liveRegion); liveProgress.hidden = true;
  liveProgress.setAttribute("aria-label", "ComfyUI node progress");
  const liveProgressText = el("p", "", liveRegion); liveProgressText.className = "render-live-progress-text";
  const liveMediaHost = el("div", null, liveRegion);
  const liveCancel = button("live-cancel", "Cancel render", () => live && cancelExact({
    projectId: live.projectId, generationId: live.generationId, saved: true }, live.id), liveRegion);
  const submissions = el("details"); submissions.id = "takes-render-history"; submissions.open = false;
  const submissionSummary = el("summary", "ComfyUI render history", submissions);
  const submissionList = el("div", null, submissions);
  takeSelector.addEventListener("change", () => {
    inspectedTakeId = takeSelector.value; previewId = null; render();
  });
  source.addEventListener("change", () => { if (source.value !== "comfyui_submission") submission.value = ""; render(); });
  submission.addEventListener("change", render);
  file.addEventListener("change", render);

  function options() {
    const prior = source.value, previousSubmission = submission.value;
    const labels = renderSubmissionLabels(state?.renderSubmissions);
    source.replaceChildren();
    const choices = [["", "Choose provenance…"], ["external_unknown", "External / unknown source"]];
    if (selection?.selected) choices.push(["selected_prompt", "From the selected generated H3 prompt (Use for execution)"]);
    if (state?.renderSubmissions.length) choices.push(["comfyui_submission", "From a recorded ComfyUI submission"]);
    for (const [value, title] of choices) { const option = el("option", title, source); option.value = value; }
    source.value = choices.some(([value]) => value === prior) ? prior : "";
    submission.replaceChildren();
    const empty = el("option", "Choose a submission…", submission); empty.value = "";
    for (const item of state?.renderSubmissions || []) {
      const option = el("option", labels.get(item.id), submission); option.value = item.id;
    }
    submission.value = state?.renderSubmissions.some(item => item.id === previousSubmission) ? previousSubmission : "";
  }

  function render() {
    updateLiveDom();
    const c = getContext(), ready = saved(c) && state;
    renderObservedEndState({ root: observationRoot, state, context: c, busy, api, settings: getSettings(), act: actObserved,
      confirm: confirmDelete, draftStore: observationDraft, analysisError,
      onAnalyzeStart: () => {
        analysisError = "";
        const activeConflict = providerActivity.conflict("analyze-selected-take", "Selected Take");
        if (activeConflict) { analysisError = activeConflict; render(); return false; }
        if (!canChangeObservedState(getContext())) {
          error = "Wait for the Story proposal request to finish before changing selected Take or observed Story evidence.";
          render(); return false;
        }
        if (busy) {
          analysisError = providerActivity.active()?.id === "analyze-selected-take"
            ? "Analyzing Selected Take is already running."
            : "A Rendered Takes action is already running.";
          render(); return false;
        }
        const controller = new AbortController(); let cancelled = false, token;
        const label = "Selected Take";
        token = beginProviderActivity(providerActivity, { id: "analyze-selected-take", label, requestLabel: "Analyze Selected Take", owner: "rendered-takes", verb: "Analyzing",
          cancel: () => {
            cancelled = true; controller.abort(); ++epoch; busy = false; analysisError = "Analysis cancelled.";
            providerActivity.finish(token); render(); schedulePolling();
          } }, message => { analysisError = message; });
        if (!token) {
          analysisError = providerActivity.conflict("analyze-selected-take", label) || "Cannot analyze while another request is running.";
          render(); return false;
        }
        return { signal: controller.signal, cancelled: () => cancelled, finish: () => providerActivity.finish(token) };
      }, onAnalyzeError: message => { analysisError = message; } });
    status.textContent = error || (busy ? "Updating rendered outputs…" : !saved(c) ? "Save a filesystem Project to import rendered Takes."
      : !state ? "Loading Takes…" : !state.takes.length ? "No rendered Takes yet. Import an actual video to begin." : `${state.takes.length} Take(s)`);
    form.disabled = !ready || busy; refreshButton.disabled = busy || !saved(c);
    source.disabled = file.disabled = !ready || busy;
    submission.disabled = !ready || busy;
    submission.hidden = submissionLabel.hidden = source.value !== "comfyui_submission";
    sourceNote.textContent = source.value === "selected_prompt"
      ? "Uses the immutable currently selected execution prompt as provenance."
      : source.value === "external_unknown" ? "Prompt provenance remains unknown. The video is still a valid Take."
      : source.value === "comfyui_submission" ? "Prompt provenance is copied from this recorded submission." : "Choose the source explicitly; filenames and dates do not establish provenance.";
    add.disabled = !ready || busy || !file.files?.[0] || !source.value || (source.value === "comfyui_submission" && !submission.value);
    clear.disabled = busy || !state?.selectedTakeId;
    continueTake.disabled = !ready || busy || continuing || !onContinue;
    submissionList.replaceChildren();
    const labels = renderSubmissionLabels(state?.renderSubmissions);
    const renderHistory = state?.renderSubmissions || [];
    const activeCounts = ["running", "queued", "unknown"].map(status => {
      const count = renderHistory.filter(item => (item.job?.status || "unknown") === status).length;
      return count ? `${count} ${status}` : "";
    }).filter(Boolean);
    const latest = [...renderHistory].sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)) || String(b.id).localeCompare(String(a.id)))[0];
    const latestStatus = latest?.job?.status;
    submissionSummary.textContent = `ComfyUI render history (${renderHistory.length})${activeCounts.length ? ` · ${activeCounts.join(" · ")}`
      : ["failed", "cancelled"].includes(latestStatus) ? ` · latest ${latestStatus}` : ""}`;
    const retriedIds = new Set((state?.renderSubmissions || []).map(item => item.retryOfSubmissionId).filter(Boolean));
    for (const item of state?.renderSubmissions || []) {
      const card = el("article", null, submissionList); card.className = "render-submission";
      el("p", labels.get(item.id), card);
      const job = item.job || { status: "unknown", outputs: [] };
      const imported = (job.outputs || []).some(output => state.takes.some(t => t.id === output.importedTakeId));
      const title = { queued: "Queued", running: "Running", completed: "Completed", failed: "Failed", cancelled: "Cancelled", unknown: "Unknown" }[job.status] || "Unknown";
      el("p", `${title}${imported ? " · Take imported" : job.status === "completed" && !job.outputs?.length && !job.message ? " — no retrievable video output" : ""}`, card);
      if (api.recoverRenderSubmission && (job.status !== "completed" || !job.outputs?.length || job.outputs.some(output => !output.importedTakeId))) {
        button(`recover-${item.id}`, "Recover exact status", () => act(c => api.recoverRenderSubmission(c.projectId, c.generationId, item.id),
          { onSuccess: response => onReconciled(response, c) }), card).disabled = busy;
      }
      if (retriedIds.has(item.id)) {
        const child = state.renderSubmissions.find(candidate => candidate.retryOfSubmissionId === item.id);
        el("p", `Retried as ${(child?.id || "").slice(0, 8)}`, card);
      } else if (api.retryRenderSubmission && ["failed", "cancelled"].includes(job.status)) {
        const retry = button(`retry-${item.id}`, retryingIds.has(item.id) ? "Retry being submitted…" : "Retry exact attempt", async () => {
          if (busy || retryingIds.has(item.id)) { error = "Retry is already being submitted."; render(); return; }
          if (!confirmRetry("Create a NEW ComfyUI job? The old failed/cancelled attempt remains. Retry uses the same selected prompt, Workflow Profile, and recorded renderer seed when available.")) return;
          const context = { ...getContext() }, identity = contextKey(context);
          retryingIds.add(item.id); busy = true; error = ""; render();
          try {
            const receipt = await api.retryRenderSubmission(context.projectId, context.generationId, item.id);
            onMutation(receipt, context);
            if (contextKey(getContext()) !== identity) return;
            if (!receipt.renderSubmissionId || !receipt.promptId) throw new Error("Submission status is unknown. Check ComfyUI before sending again.");
            await watchSubmitted(receipt, context);
            if (receipt.provenanceWarning) error = receipt.provenanceWarning;
          } catch (e) { if (contextKey(getContext()) === identity) error = e.message; }
          finally { retryingIds.delete(item.id); busy = false; render(); schedulePolling(); }
        }, card);
        retry.disabled = busy || retryingIds.has(item.id);
      }
      if (!["completed", "failed", "cancelled"].includes(job.status)) {
        const watching = live?.id === item.id && live.generationId === c.generationId;
        button(`watch-${item.id}`, watching && live.status !== "disconnected" ? "Watching" : "Watch / Reconnect",
          () => watch(item.id, "explicit"), card).disabled = watching && live.status !== "disconnected";
        button(`cancel-${item.id}`, cancellingIds.has(item.id) ? "Cancel requested…" : "Cancel render",
          () => cancelExact({ ...c }, item.id), card).disabled = cancellingIds.has(item.id);
      }
      if (job.message) el("p", job.message, card);
      for (const output of job.outputs || []) {
        const row = el("div", null, card);
        el("p", `${output.nodeId ? `Node ${output.nodeId} · ` : ""}${output.subfolder ? `${output.subfolder}/` : ""}${output.filename} · ${output.type}`, row);
        if (output.importedTakeId) el("p", state.takes.some(t => t.id === output.importedTakeId) ? "Take imported" : "Previously imported Take was deleted", row);
        else button(`output-${item.id}-${output.id}`, "Import output", () => act(c => api.importRenderOutput(c.projectId, c.generationId, item.id, output.id)), row).disabled = busy;
      }
      const details = el("details", null, card); el("summary", "Technical details", details);
      el("p", `ComfyUI Prompt ID: ${item.comfyPromptId}`, details);
      el("p", `RenderSubmission ID: ${item.id}`, details);
      if (job.failedNodeId) el("p", `Failed node: ${job.failedNodeId}`, details);
    }
    if (!state?.renderSubmissions.length) el("p", "No recorded render submissions.", submissionList);
    const takes = state?.takes || [];
    if (takes.length && !takes.some(take => take.id === inspectedTakeId)) {
      inspectedTakeId = takes.find(take => take.id === state.selectedTakeId)?.id
        || [...takes].sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)) || String(b.id).localeCompare(String(a.id)))[0].id;
      previewId = null;
    }
    takeSelector.replaceChildren();
    for (const take of [...takes].sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)) || String(b.id).localeCompare(String(a.id)))) {
      const origin = take.sourceKind === "comfyui_submission"
        ? labels.get(take.renderSubmissionId) || "Send provenance unavailable"
        : take.sourceKind === "external_unknown" ? "External / unknown" : "Selected H3 prompt";
      const option = el("option", `${take.id === state.selectedTakeId ? "✓ SELECTED · " : ""}${take.originalFilename} · ${formatLocalTimestamp(take.createdAt)} · ${origin}${take.availability === "missing" ? " · Media missing" : ""}`, takeSelector);
      option.value = take.id;
    }
    takeSelector.value = inspectedTakeId || ""; takeSelector.disabled = !ready || busy || !takes.length;
    list.replaceChildren();
    const previewTake = state?.takes.find(t => t.id === previewId && t.availability === "available");
    const previewUrl = previewTake ? api.takeMediaUrl(c.projectId, c.generationId, previewId) : null;
    if (preview.dataset?.url !== previewUrl) {
      preview.querySelector("video")?.pause?.();
      preview.replaceChildren();
      if (preview.dataset) preview.dataset.url = previewUrl || "";
    }
    const take = state?.takes.find(item => item.id === inspectedTakeId);
    if (take) {
      const card = el("article", null, list); card.className = "take-card";
      el("strong", take.originalFilename, card);
      if (take.id === state.selectedTakeId) el("span", "Selected Take", card).className = "take-selected";
      el("p", `${formatLocalTimestamp(take.createdAt)} · ${(take.byteSize / 1024 / 1024).toFixed(2)} MiB`, card);
      const label = { external_unknown: "External / unknown source", selected_prompt: "Selected generated H3 prompt", comfyui_submission: "Recorded ComfyUI submission" }[take.sourceKind];
      el("p", `${label} · Prompt provenance: ${take.promptProvenance}`, card);
      if (take.renderSubmissionId) el("p", labels.get(take.renderSubmissionId) || "Send provenance unavailable", card);
      const technical = el("details", null, card); el("summary", "Technical details", technical);
      el("p", `Take ID: ${take.id}`, technical);
      if (take.renderSubmissionId) el("p", `RenderSubmission ID: ${take.renderSubmissionId}`, technical);
      if (take.comfyPromptId) el("p", `ComfyUI Prompt ID: ${take.comfyPromptId}`, technical);
      const actions = el("div", null, card); actions.className = "take-actions";
      if (take.sourceFingerprint && selection?.currentFingerprint)
        el("p", take.sourceFingerprint === selection.currentFingerprint ? "Source prompt matches current saved authoring."
          : "Source prompt no longer matches current saved authoring.", card);
      if (take.availability === "missing") {
        el("p", "Media is missing. Reattach the original file with matching SHA-256.", card);
        const label = el("label", "Reattach missing media", card), replacement = el("input", null, label);
        replacement.type = "file"; replacement.accept = file.accept; replacement.disabled = busy;
        replacement.addEventListener("change", () => {
          const selected = replacement.files?.[0];
          if (selected) return act(c => api.reattachTake(c.projectId, c.generationId, take.id, selected));
        });
      } else {
        button(`preview-${take.id}`, previewId === take.id ? "Close preview" : "Preview", () => {
          previewId = previewId === take.id ? null : take.id; render();
        }, actions).disabled = busy;
      }
      button(`select-${take.id}`, "Select Take", () => actObserved(c => api.selectTake(c.projectId, c.generationId, take.id)), actions).disabled = busy || take.id === state.selectedTakeId;
      button(`delete-${take.id}`, "Delete Take", async () => {
        if (take.id === state.selectedTakeId && !observedChangeAllowed()) return;
        if (await confirmDelete(`Delete Take “${take.originalFilename}”? Its media is removed when no other Take in this Project references it.`))
          return (take.id === state.selectedTakeId ? actObserved : act)(c => api.deleteTake(c.projectId, c.generationId, take.id));
      }, actions).disabled = busy;
      if (take.promptProvenance === "known") button(`provenance-${take.id}`, "View prompt provenance", async () => {
        const ticket = epoch, context = { ...getContext() };
        try {
          const detail = await api.takeDetail(context.projectId, context.generationId, take.id);
          if (ticket !== epoch || contextKey(getContext()) !== key || inspectedTakeId !== take.id || !list.querySelector(".take-card") || list.querySelector(".take-card") !== card) return;
          const details = el("pre", JSON.stringify(detail.promptSnapshot, null, 2), card); details.className = "execution-prompt";
        } catch (e) { if (ticket === epoch) { error = e.message; render(); } }
      }, actions).disabled = busy;
      if (previewId === take.id && take.availability === "available" && !preview.querySelector("video")) {
        el("p", take.originalFilename, preview);
        const video = el("video", null, preview); video.controls = true; video.preload = "metadata";
        video.src = api.takeMediaUrl(c.projectId, c.generationId, take.id);
        el("p", "Preview support depends on your browser and the video codec. Original bytes are preserved.", preview).className = "helper";
      }
    }
  }

  async function cancelExact(c, id) {
    if (disposed || cancellingIds.has(id) || !api.cancelRenderSubmission) return;
    cancellingIds.add(id); render();
    try {
      const response = await api.cancelRenderSubmission(c.projectId, c.generationId, id);
      onMutation(response, c);
      if (!mounted() || !sameLiveProject(getContext(), c)) return;
      advanceOutputs(c.generationId);
      rememberOutputs(response, c);
      if (getContext().generationId === c.generationId) { state = response; options(); }
      error = response.cancelDiagnosis || (response.cancelSupported === false ? "Exact-job cancellation is unavailable on this ComfyUI server." : "");
      if (response.cancelDispatched && live?.id === id && live.generationId === c.generationId) {
        live.cancelRequested = true;
        live.status = "cancel dispatched; awaiting renderer";
      }
      if (!response.cancelDiagnosis && response.cancelSupported && !response.cancelDispatched && !response.alreadyTerminal)
        error = "No cancellation was dispatched; exact renderer status was checked.";
      onReconciled(response, c);
      await readForeground(c);
      ensureLiveObservation();
    } catch (e) { if (sameLiveProject(getContext(), c)) error = e.message; }
    finally { cancellingIds.delete(id); if (mounted()) { render(); schedulePolling(); } }
  }

  async function act(action, { onError, onSuccess, onSettled } = {}) {
    if (disposed || busy || !saved(getContext())) { onSettled?.(); return; }
    stopPolling();
    const c = { ...getContext() }, ticket = ++epoch; busy = true; error = ""; render();
    try {
      const response = await action(c);
      // Project fingerprint receipts are Project-wide even when this Generation
      // is no longer visible. Notify before discarding stale Generation UI.
      onMutation(response, c);
      if (disposed || ticket !== epoch || contextKey(c) !== contextKey(getContext())) return;
      state = response; rememberOutputs(response, c); error = response.reconciliationError || ""; options(); onSuccess?.(response); return true;
    } catch (e) { if (ticket === epoch) { error = e.message; onError?.(e.message); } }
    finally {
      onSettled?.();
      if (!disposed && ticket === epoch) { busy = false; ensureLiveObservation(); render(); schedulePolling(); }
    }
  }
  async function importCurrent() {
    if (add.disabled) return;
    const chosenFile = file.files[0], provenance = { sourceKind: source.value };
    if (source.value === "comfyui_submission") provenance.renderSubmissionId = submission.value;
    if (source.value === "selected_prompt") provenance.selectedPromptId = selection.selected.id;
    const previousIds = new Set((state?.takes || []).map(take => take.id));
    const imported = await act(c => api.importTake(c.projectId, c.generationId, chosenFile, provenance), {
      onSuccess: response => {
        const added = response.takes.filter(take => !previousIds.has(take.id));
        // Manual imports append one distinct Take, even when managed bytes are reused.
        if (added.length === 1) { inspectedTakeId = added[0].id; previewId = null; }
      },
    });
    if (imported) file.value = "";
    render();
  }
  async function refresh({ pendingOnly = false } = {}) {
    if (disposed) return;
    stopPolling();
    const c = { ...getContext() };
    if (c.replacing) return;
    if (busy && key === contextKey(c)) {
      if (historyTicket === epoch) {
        const ticket = epoch;
        try { await refreshForeground(c); }
        catch (e) { if (ticket === epoch) error = e.message; }
        finally { if (ticket === epoch) { ensureLiveObservation(); render(); schedulePolling(); } }
      }
      return;
    }
    if (foregroundProjectId !== c.projectId || !c.saved) {
      clearLive(); foregroundRows = []; outputVersions.clear(); discoveryCursor = 0; foregroundUpdatedAt = null; foregroundProjectId = c.saved ? c.projectId : null;
    }
    if (key !== contextKey(c)) { state = selection = null; inspectedTakeId = null; previewId = null; submissions.open = false; }
    key = contextKey(c); const ticket = ++epoch;
    if (!saved(c)) { busy = false; historyTicket = null; state = selection = null; options(); render(); return; }
    busy = false; historyTicket = null; error = "";
    try {
      const version = outputVersion(c.generationId);
      // Foreground selection must not wait for local/history reads or downloads.
      // Observe local read failures immediately, even while discovery is pending.
      const local = Promise.all([api.generationOutputs(c.projectId, c.generationId),
        api.executionSelection(c.projectId, c.generationId)]);
      local.catch(() => {});
      try { await refreshForeground(c); }
      catch (e) { if (ticket === epoch) error = e.message; }
      if (disposed || ticket !== epoch || key !== contextKey(getContext())) return;
      schedulePolling();
      const [outputs, selected] = await local;
      if (disposed || ticket !== epoch || key !== contextKey(getContext())) return;
      // Exact terminal/cancel reconciliation can finish while this read is in flight.
      // Do not replace that newer HTTP truth with the older response snapshot.
      if (version === outputVersion(c.generationId) || !state) state = outputs;
      selection = selected; options();
      rememberOutputs(state, c);
      const items = [...(state.renderSubmissions || []).map(item => ({ ...item, generationId: c.generationId })),
        ...foregroundRows.filter(item => item.generationId !== c.generationId && activeSubmission(item)
          && (api.foregroundRenders || live?.id === item.id))];
      const unique = [...new Map(items.map(item => [JSON.stringify([item.generationId, item.id]), item])).values()];
      busy = Boolean(api.reconcileRenderSubmission && unique.length);
      historyTicket = busy ? ticket : null; render(); schedulePolling();
      // Sequential bounded polling, with a fresh context check before every request.
      // Terminal refresh uses persisted candidates and never resubmits the workflow.
      for (const item of unique) {
        if (!api.reconcileRenderSubmission || (pendingOnly && ["completed", "failed", "cancelled"].includes(item.job?.status))) continue;
        if (!pageVisible() || ticket !== epoch || key !== contextKey(getContext())) return;
        const exact = { ...c, generationId: item.generationId };
        const version = outputVersion(item.generationId);
        const response = await api.reconcileRenderSubmission(c.projectId, item.generationId, item.id);
        // An exact terminal/cancel response can supersede this poll's entire
        // snapshot, including its Project fingerprint, while HTTP is in flight.
        if (version !== outputVersion(item.generationId)) continue;
        if (response.projectUpdatedAt && foregroundUpdatedAt
            && Date.parse(response.projectUpdatedAt) < Date.parse(foregroundUpdatedAt)) continue;
        onMutation(response, exact);
        if (disposed || ticket !== epoch || key !== contextKey(getContext())) return;
        rememberOutputs(response, exact);
        if (item.generationId === c.generationId) { state = response; options(); }
        error = response.reconciliationError || error; render();
        onReconciled(response, exact);
      }
      await refreshForeground(c);
    } catch (e) { if (ticket === epoch) error = e.message; }
    finally { if (!disposed && ticket === epoch) { busy = false; historyTicket = null; ensureLiveObservation(); render(); schedulePolling(); } }
  }
  async function refreshForeground(c) {
    if (!api.foregroundRenders) return;
    const identity = contextKey(c);
    if (foregroundPending?.key === identity) return foregroundPending.promise;
    const request = { key: identity };
    request.promise = readForeground(c, true).then(() => {
      if (mounted() && identity === contextKey(getContext())) { ensureLiveObservation(); render(); }
    }).catch(e => { if (mounted() && identity === contextKey(getContext())) foregroundUnavailable = true; throw e; }).finally(() => { if (foregroundPending === request) foregroundPending = null; });
    foregroundPending = request;
    return request.promise;
  }
  function sync() {
    if (disposed) return;
    if (getContext().replacing) return;
    if (key !== contextKey(getContext())) {
      if (live && !sameLiveProject(getContext(), live)) clearLive();
      stopPolling();
      state = selection = null; previewId = null; inspectedTakeId = null; submissions.open = false; busy = false; error = "";
      analysisError = "";
      observationDraft.key = ""; observationDraft.value = null; observationDraft.dirty = false; observationDraft.editError = "";
      historyTicket = null;
      source.value = submission.value = file.value = "";
      void refresh();
    }
    render();
  }
  function observedChangeAllowed() {
    if (!canChangeObservedState(getContext())) {
      error = "Wait for the Story proposal request to finish before changing selected Take or observed Story evidence.";
      render();
      return false;
    }
    return true;
  }
  function actObserved(action, options) {
    if (!observedChangeAllowed()) return false;
    return act(action, options);
  }
  const visibilityChanged = () => { if (pageVisible()) schedulePolling(); else stopPolling(); };
  doc.addEventListener?.("visibilitychange", visibilityChanged);
  const pageHidden = () => { stopPolling(); clearLive(); ++epoch; busy = false; };
  doc.defaultView?.addEventListener("pagehide", pageHidden);
  doc.defaultView?.addEventListener("pageshow", visibilityChanged);
  function dispose() {
    disposed = true; stopPolling(); clearLive(); ++epoch;
    doc.removeEventListener?.("visibilitychange", visibilityChanged);
    doc.defaultView?.removeEventListener("pagehide", pageHidden);
    doc.defaultView?.removeEventListener("pageshow", visibilityChanged);
  }
  function reset() {
    ++resetEpoch;
    stopPolling(); clearLive(); ++epoch; key = ""; busy = false;
    foregroundProjectId = null; foregroundRows = []; foregroundUnavailable = false; outputVersions.clear(); discoveryCursor = 0; foregroundUpdatedAt = null;
    foregroundPending = null; historyTicket = null;
    state = selection = null; inspectedTakeId = previewId = null; error = analysisError = "";
    observationDraft.key = ""; observationDraft.value = null; observationDraft.dirty = false; observationDraft.editError = "";
    source.value = submission.value = file.value = ""; submissions.open = false;
    cancellingIds.clear(); retryingIds.clear(); options(); render();
  }
  return { sync, refresh, reset, watchSubmitted, dispose };
}
