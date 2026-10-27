// Ephemeral candidate; shared provider activity and navigation authority own pending work.
export function createContinuationDrafting({ api, activity, navigationAuthority, getContext, getSettings,
  getCreativeRequest, applyCreativeRequest, confirm, present, notify }) {
  let draft = null;
  let applying = false;
  const capture = () => {
    const c = { ...getContext() };
    return { ...c, ticket: navigationAuthority.capture(c.projectId, c.generationId) };
  };
  const owns = c => {
    const now = getContext();
    return now.eligible && !now.replacing && navigationAuthority.ownsContext(c.ticket, now.projectId, now.generationId)
      && (c.projectEditFingerprint || c.projectFingerprint) === (now.projectEditFingerprint || now.projectFingerprint);
  };
  return {
    async generate(direction = "") {
      const c = capture();
      if (!c.eligible || c.replacing) return;
      const conflict = activity.conflict("continuation-draft", "Draft Continuation");
      if (conflict) { notify(conflict); return; }
      const controller = new AbortController();
      const token = activity.begin({ id: "continuation-draft", label: "Draft Continuation", owner: "app",
        cancel: () => controller.abort() });
      if (!token) return;
      draft = null; present(null);
      try {
        const result = await api.draftContinuation(c.projectId, c.generationId, {
          expectedProjectFingerprint: c.projectFingerprint, direction, llm: getSettings(),
        }, controller.signal);
        if (controller.signal.aborted || !owns(c)) return;
        if (result.authority.projectId !== c.projectId || result.authority.generationId !== c.generationId
          || result.authority.projectEditFingerprint !== (c.projectEditFingerprint || c.projectFingerprint)
          || !/^[0-9a-f]{64}$/.test(result.authority.evidenceRevision || "")) throw new Error("Draft authority changed; draft again.");
        draft = { context: c, result };
        present(result);
      } catch (error) {
        if (!controller.signal.aborted && owns(c)) notify(`Could not draft continuation: ${error.message}`);
      } finally { activity.finish(token); }
    },
    async apply(candidate, getCurrentCandidate = () => candidate) {
      if (applying) return;
      const selected = draft;
      if (!selected || !owns(selected.context) || !candidate.trim()) {
        notify("Continuation draft is stale or empty; draft again."); return;
      }
      applying = true;
      try {
        const before = getCreativeRequest();
        if (before.trim() && !await confirm("Replace the current Creative Request, including any unsaved edits, with this edited continuation draft?")) return;
        if (draft !== selected || !owns(selected.context) || getCreativeRequest() !== before) return;
        await api.validateContinuationDraft(selected.context.projectId, selected.context.generationId, selected.result.authority);
        if (draft !== selected || !owns(selected.context) || getCreativeRequest() !== before
          || getCurrentCandidate() !== candidate) return;
        applyCreativeRequest(candidate.trim());
        draft = null; present(null);
        notify("Continuation applied to Main Prompt / Idea. Review and Save or Generate from Main Prompt explicitly. Story Generate still uses Scene Plan.");
      } catch (error) {
        if (error.status === 409 && draft === selected) { draft = null; present(null); }
        notify(`Could not apply continuation: ${error.message}`);
      }
      finally { applying = false; }
    },
    clear() { draft = null; present(null); },
  };
}

export function mountContinuationDrafting({ root, ...options }) {
  const doc = root.ownerDocument;
  const node = (tag, text) => { const el = doc.createElement(tag); el.textContent = text || ""; root.append(el); return el; };
  const directionLabel = node("label", "Optional next action / direction");
  const direction = doc.createElement("textarea"); direction.rows = 2; direction.maxLength = 10000;
  direction.placeholder = "Continue naturally, or describe what happens next…"; directionLabel.append(direction);
  const generate = node("button", "Draft Continuation"); generate.type = "button";
  const status = node("p", "Uses the immediate predecessor’s Selected Take and saved evidence. Review before applying."); status.className = "helper";
  const preview = node("textarea"); preview.rows = 7; preview.maxLength = 20000; preview.hidden = true;
  preview.setAttribute("aria-label", "Editable continuation candidate");
  const inspect = node("details"); inspect.hidden = true;
  const summary = doc.createElement("summary"); summary.textContent = "Inspect source evidence and authority"; inspect.append(summary);
  const evidence = doc.createElement("pre"); evidence.className = "execution-prompt"; inspect.append(evidence);
  const apply = node("button", "Apply to Creative Request"); apply.type = "button"; apply.hidden = true;
  const control = createContinuationDrafting({ ...options, present(result) {
    preview.hidden = apply.hidden = inspect.hidden = !result;
    preview.value = result?.candidate || "";
    evidence.textContent = result ? JSON.stringify(result.evidence, null, 2) : "";
    status.textContent = result ? [...result.warnings, "Editable candidate only. Apply changes the local Main Prompt; Save and Generate remain explicit."].join(" ")
      : "Uses saved source evidence. Analyze Selected Take explicitly for stronger evidence when needed.";
  } });
  generate.addEventListener("click", () => control.generate(direction.value));
  apply.addEventListener("click", () => control.apply(preview.value, () => preview.value));
  options.activity.subscribe(active => { generate.disabled = Boolean(active); apply.disabled = Boolean(active); });
  let contextKey = "";
  return { sync() {
    const c = options.getContext();
    root.hidden = !c.eligible;
    const key = JSON.stringify([c.projectId, c.generationId, c.projectEditFingerprint || c.projectFingerprint]);
    if (contextKey !== key) { contextKey = key; direction.value = ""; control.clear(); }
  } };
}
