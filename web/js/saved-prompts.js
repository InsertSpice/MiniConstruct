import { formatLocalTimestamp } from "./local-time.js";

// Save targets belong to visible candidate lineage; list selection is inspection only.
export function createSavedPrompts({ root, api, getContext, getOutput, setOutput, notify = () => {}, confirmReplace = message => confirm(message) }) {
  const save = root.querySelector("#save-h3-prompt");
  const saveAsNew = root.querySelector("#save-h3-prompt-as-new");
  const list = root.querySelector("#saved-h3-prompts");
  const loadButton = root.querySelector("#load-saved-h3-prompt");
  const deleteButton = root.querySelector("#delete-saved-h3-prompt");
  const status = root.querySelector("#saved-h3-prompt-status");
  const details = root.querySelector("#saved-h3-prompt-details");
  const doc = root.ownerDocument || root;
  const detail = (tag, text, className = "") => {
    const node = doc.createElement(tag); node.textContent = text; node.className = className; details.append(node);
  };
  const memory = new Map();
  let loadedKey = "", loadingKey = "", loadError = "", errorKey = "", prompts = [], activeId = null, selectedId = null, busy = false, ticket = 0, limit = 100;
  const key = c => c?.projectId && c?.generationId ? `${c.projectId}/${c.generationId}` : "";
  const candidate = saved => ({ prompt: saved.prompt, authoringPath: saved.authoringPath, origin: "manual_edit",
    sourceFingerprint: null, validation: null, generationStatus: "complete", savedPromptTarget: { key: key(getContext()), id: saved.id } });
  const editTarget = output => output?.savedPromptTarget?.key === key(getContext()) ? output.savedPromptTarget.id : null;

  function sync() {
    const c = getContext(), active = Boolean(c?.saved && key(c)), output = getOutput();
    const loading = active && loadingKey === key(c);
    const disabled = busy || loading || c?.candidateBusy || !active || loadedKey !== key(c);
    const atCapacity = prompts.length >= limit;
    const canReuse = prompts.some(item => item.prompt === output?.prompt);
    save.disabled = disabled || !output?.prompt || (atCapacity && !editTarget(output) && !canReuse);
    if (saveAsNew) saveAsNew.disabled = disabled || !output?.prompt || atCapacity;
    const capacityGuidance = `This Generation has ${limit} saved H3 prompts. Delete an older entry before saving another.`;
    save.title = atCapacity && !editTarget(output) && !canReuse ? capacityGuidance : "Update the loaded saved prompt, or create its first save.";
    if (saveAsNew) saveAsNew.title = atCapacity ? capacityGuidance : "Always create a new Saved H3 Prompt entry.";
    list.disabled = disabled || !prompts.length;
    loadButton.disabled = disabled || !selectedId;
    deleteButton.disabled = disabled || !selectedId;
    list.replaceChildren();
    for (const item of active && loadedKey === key(c) ? prompts : []) {
      const option = root.ownerDocument?.createElement("option") || root.createElement("option");
      option.value = item.id;
      option.textContent = `${item.id === activeId ? "✓ ACTIVE · " : ""}Save ${item.saveNumber} · ${formatLocalTimestamp(item.savedAt)} · ${item.authoringPath === "story" ? "Story" : "Main Prompt"} · ${item.stale ? "Stale" : "Current"}`;
      list.append(option);
    }
    list.value = selectedId || "";
    details.replaceChildren();
    const inspected = active && loadedKey === key(c) && prompts.find(item => item.id === selectedId);
    if (inspected) {
      detail("strong", `Save ${inspected.saveNumber}${inspected.id === activeId ? " · Active restore prompt" : ""}`);
      const savedTime = new Date(inspected.savedAt);
      detail("p", Number.isNaN(savedTime.getTime()) ? formatLocalTimestamp(inspected.savedAt) : savedTime.toLocaleString());
      detail("p", `${inspected.authoringPath === "story" ? "Story" : "Main Prompt"} · ${inspected.stale ? "Stale source" : "Current source"}`);
      const excerpt = Array.from(inspected.prompt.replace(/\s+/g, " ").trim());
      detail("p", excerpt.slice(0, 240).join("") + (excerpt.length > 240 ? "…" : ""), "saved-prompt-preview");
    }
    if (!active) status.textContent = "Save a filesystem Project to save H3 prompts.";
    else if (loading) status.textContent = "Loading saved prompts…";
    else if (loadedKey !== key(c)) status.textContent = errorKey === key(c) && loadError
      ? loadError : "Saved prompts are unavailable until this Generation is loaded.";
    else if (!prompts.length) status.textContent = "No saved prompts";
    else {
      const matching = prompts.find(item => item.prompt === output?.prompt);
      status.textContent = matching ? `Saved prompt loaded${matching.stale ? " · saved source is stale" : ""}` : "Unsaved changes";
    }
  }

  function apply(response, target, preferredId = null) {
    prompts = response.prompts; limit = response.limit || 100; activeId = response.activeSavedPromptId; loadedKey = target;
    selectedId = prompts.some(item => item.id === preferredId) ? preferredId
      : prompts.some(item => item.id === selectedId) ? selectedId : activeId || prompts[0]?.id || null;
  }

  function rememberCurrent() {
    const c = getContext(), output = getOutput();
    if (c?.saved && key(c) && output) memory.set(key(c), output);
  }

  async function load({ restore = true } = {}) {
    const c = { ...getContext() }, target = key(c), ownTicket = ++ticket;
    prompts = []; activeId = null; selectedId = null; loadedKey = "";
    loadError = ""; errorKey = "";
    if (!c.saved || !target) { loadingKey = ""; sync(); return; }
    loadingKey = target; sync();
    try {
      const response = await api.savedH3Prompt(c.projectId, c.generationId);
      if (ownTicket !== ticket || key(getContext()) !== target) return;
      apply(response, target);
      if (restore && !getOutput()) {
        const saved = prompts.find(item => item.id === activeId);
        const restored = memory.get(target) || (saved && candidate(saved));
        if (restored) setOutput(restored);
      }
      const output = getOutput();
      if (editTarget(output) && !prompts.some(item => item.id === editTarget(output))) delete output.savedPromptTarget;
    } catch (error) {
      if (ownTicket === ticket && key(getContext()) === target) {
        loadError = `Could not load saved H3 prompts: ${error.message}`;
        errorKey = target;
        notify(loadError);
      }
    } finally {
      if (ownTicket === ticket) { loadingKey = ""; sync(); }
    }
  }

  async function saveCurrent(asNew = false) {
    asNew = asNew === true;
    const c = { ...getContext() }, target = key(c), output = getOutput(), ownTicket = ticket;
    if ((asNew ? saveAsNew?.disabled : save.disabled) || !output?.prompt) return;
    const savedPromptId = asNew ? null : editTarget(output);
    // Manual edits copy this lineage reference. A successful save retargets those
    // descendants too, while independently generated/replaced candidates lack it.
    const saveTarget = { key: target, id: editTarget(output) };
    output.savedPromptTarget = saveTarget;
    busy = true; sync();
    try {
      const response = await api.saveH3Prompt(c.projectId, c.generationId,
        { prompt: output.prompt, authoringPath: output.authoringPath || "one_shot",
          ...(asNew ? { saveAsNew: true } : savedPromptId ? { savedPromptId } : {}) });
      saveTarget.id = response.activeSavedPromptId;
      if (ticket === ownTicket && key(getContext()) === target) {
        apply(response, target, response.activeSavedPromptId);
        const saved = prompts.find(item => item.id === response.activeSavedPromptId);
        notify(savedPromptId ? `Updated Save ${saved.saveNumber}` : response.alreadySaved
          ? `This exact H3 prompt is already saved as Save ${saved.saveNumber}.` : `Saved as Save ${saved.saveNumber}`);
      }
    } catch (error) { notify(`Could not save H3 prompt: ${error.message}`); }
    finally {
      if (!saveTarget.id) {
        if (output.savedPromptTarget === saveTarget) delete output.savedPromptTarget;
        const current = getOutput();
        if (current?.savedPromptTarget === saveTarget) delete current.savedPromptTarget;
      }
      busy = false; sync();
    }
  }

  async function loadSelected() {
    if (loadButton.disabled) return;
    const c = { ...getContext() }, target = key(c), ownTicket = ticket;
    const saved = prompts.find(item => item.id === selectedId), output = getOutput(), visibleText = output?.prompt;
    if (!saved) return;
    if (output?.prompt && output.prompt !== saved.prompt && !prompts.some(item => item.prompt === output.prompt)
      && !confirmReplace("Load this saved H3 prompt and discard unsaved changes to the visible text?")) return;
    busy = true; sync();
    try {
      const response = await api.loadH3Prompt(c.projectId, c.generationId, saved.id);
      if (ticket !== ownTicket || key(getContext()) !== target) return;
      apply(response, target, saved.id);
      // A candidate arriving during the request still takes precedence.
      if (getOutput() !== output || getOutput()?.prompt !== visibleText) { notify("Visible text changed while loading; it was kept."); return; }
      setOutput(candidate(prompts.find(item => item.id === saved.id))); memory.set(target, getOutput());
      notify("Saved H3 prompt loaded.");
    } catch (error) { notify(`Could not load saved H3 prompt: ${error.message}`); }
    finally { busy = false; sync(); }
  }

  async function deleteSelected() {
    if (deleteButton.disabled) return;
    const c = { ...getContext() }, target = key(c), ownTicket = ticket, id = selectedId;
    if (!confirmReplace("Delete this saved H3 prompt? Visible text and execution selection will remain.")) return;
    busy = true; sync();
    try {
      const response = await api.deleteH3Prompt(c.projectId, c.generationId, id);
      if (ticket === ownTicket && key(getContext()) === target) {
        apply(response, target);
        const output = getOutput();
        if (editTarget(output) === id) delete output.savedPromptTarget;
        const remembered = memory.get(target);
        if (remembered?.savedPromptTarget?.id === id) delete remembered.savedPromptTarget;
        notify("Saved H3 prompt deleted.");
      }
    } catch (error) { notify(`Could not delete saved H3 prompt: ${error.message}`); }
    finally { busy = false; sync(); }
  }

  list.addEventListener("change", () => { selectedId = list.value; sync(); });
  save.addEventListener("click", () => saveCurrent());
  saveAsNew?.addEventListener("click", () => saveCurrent(true));
  loadButton.addEventListener("click", loadSelected);
  deleteButton.addEventListener("click", deleteSelected);
  sync();
  // Context adoption owns loads; execution mutations and ordinary renders only sync.
  function ensureLoaded() {
    const c = getContext(), target = key(c);
    if (!c?.saved || !target || loadedKey === target || loadingKey === target) { sync(); return; }
    return load();
  }
  function reset() {
    const output = getOutput();
    if (output) delete output.savedPromptTarget;
    ++ticket; memory.clear(); loadedKey = loadingKey = ""; prompts = []; activeId = selectedId = null; busy = false;
    loadError = errorKey = "";
  }
  return { sync, load, reset, ensureLoaded, rememberCurrent, saveCurrent, loadSelected, deleteSelected };
}
