import { PLAN_SCOPES, RELATIONSHIP_OPTIONS, recommendationAssetLabel } from "./planning-workflow.js";

export const STORY_FIELDS = [
  ["creativeRequest", "Main Prompt / Creative Request", 50000],
  ["incomingState", "Incoming State", 20000],
  ["intendedOutgoingState", "Intended Outgoing State", 20000],
  ["relationshipToPrevious", "Relationship to Previous"],
  ["cameraIntent", "Camera / Shot Intent", 20000],
];

export function storyEligible(context) {
  return context.saved && Boolean(context.projectId) && !context.dirty && context.generations.length <= 12;
}

// Draft state is ephemeral; the Project owns the separately saved Story idea.
export function createStoryPlanner({ root, api, getContext, getSettings, adoptProject,
  confirmDiscard = message => globalThis.confirm(message), onApplyBusy = () => {}, onIdeaChange = () => {} }) {
  const doc = root.ownerDocument;
  let draft = null, selected = new Set(), scopes = new Set(), busy = null, epoch = 0;
  const node = (tag, text, parent = root) => {
    const el = doc.createElement(tag);
    if (text != null) el.textContent = text;
    parent.append(el);
    return el;
  };
  const field = (key, text, tag, parent = root) => {
    const label = node("label", text, parent);
    const input = node(tag, null, label);
    input.id = `story-${key}`;
    return input;
  };
  const button = (key, text, parent = root) => {
    const el = node("button", text, parent);
    el.type = "button"; el.id = `story-${key}`;
    return el;
  };
  node("p", "Save the High-level Sequence Idea with the Project before Build. Apply attaches planned references on new Generations; existing-Generation recommendations remain advisory.").className = "helper";
  const idea = field("idea", "High-level Sequence Idea", "textarea");
  idea.rows = 4; idea.maxLength = 20000;
  const count = field("count", "Desired total Generations (2–12)", "input");
  count.type = "number"; count.min = "2"; count.max = "12"; count.step = "1"; count.value = "4";
  const timeout = field("timeout", "Planner request timeout (seconds)", "input");
  timeout.type = "number"; timeout.min = "1"; timeout.max = "600"; timeout.step = "1";
  timeout.value = "600";
  const plannerSettings = () => ({ ...getSettings(), timeoutSeconds: Number(timeout.value) });
  const build = button("build", "Build Sequence"); build.className = "primary";
  const eligibility = node("p", ""); eligibility.className = "helper";
  const status = node("p", ""); status.id = "story-status"; status.className = "helper";
  status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
  const editor = node("div"); editor.className = "story-editor"; editor.hidden = true;
  const contextInput = field("context", "Draft Sequence Context", "textarea", editor);
  contextInput.rows = 3; contextInput.maxLength = 20000;
  const cards = node("div", null, editor); cards.className = "story-cards";
  const warnings = node("div", null, editor); warnings.id = "story-warnings";
  node("p", "Select Generations above and fields below to revise. Every unselected field stays exact.", editor).className = "helper";
  const scopeGroup = node("section", null, editor); scopeGroup.className = "story-scope-group";
  const scopeHeading = node("h3", "Fields to revise", scopeGroup); scopeHeading.className = "story-subheading";
  scopeHeading.id = "story-scope-heading";
  scopeGroup.setAttribute("role", "group"); scopeGroup.setAttribute("aria-labelledby", scopeHeading.id);
  node("p", "Choose which fields the model may change; unselected fields stay exact.", scopeGroup).className = "helper";
  const scopeContainer = node("div", null, scopeGroup); scopeContainer.className = "story-scopes";
  for (const [key, label] of PLAN_SCOPES) {
    const option = node("label", null, scopeContainer); option.className = "story-scope-option";
    const input = node("input", null, option); input.type = "checkbox"; input.id = `story-scope-${key}`;
    node("span", label, option);
    input.addEventListener("change", () => {
      if (input.checked) scopes.add(key); else scopes.delete(key);
      refresh();
    });
  }
  const instruction = field("instruction", "Revision instruction", "textarea", editor);
  instruction.rows = 3; instruction.maxLength = 8000;
  const actionStatus = node("p", "", editor); actionStatus.id = "story-action-status";
  actionStatus.className = "helper story-action-status"; actionStatus.setAttribute("role", "status"); actionStatus.setAttribute("aria-live", "polite");
  const revisionActions = node("div", null, editor); revisionActions.className = "story-action-group story-revision-actions";
  const revise = button("revise", "Revise Selected", revisionActions);
  const applyActions = node("div", null, editor); applyActions.className = "story-action-group story-apply-actions";
  const apply = button("apply", "Apply Draft", applyActions); apply.className = "primary";
  const discard = button("discard", "Discard Draft", applyActions);

  function refresh() {
    const context = getContext();
    const eligible = storyEligible(context);
    const total = Number(count.value);
    const validTimeout = Number.isFinite(Number(timeout.value)) && Number(timeout.value) >= 1 && Number(timeout.value) <= 600;
    count.min = String(Math.max(2, context.generations.length));
    build.disabled = Boolean(busy) || !eligible || !validTimeout || !idea.value.trim() || !Number.isInteger(total)
      || total < Number(count.min) || total > 12;
    apply.disabled = Boolean(busy) || !draft || !eligible;
    revise.disabled = Boolean(busy) || !draft || !eligible || !validTimeout || !selected.size || !scopes.size || !instruction.value.trim();
    discard.disabled = busy === "apply";
    for (const input of root.querySelectorAll("textarea, input, select")) input.disabled = Boolean(busy);
    // First Generation never has a predecessor.
    const firstRelationship = cards.querySelector("select");
    if (firstRelationship) firstRelationship.disabled = true;
    eligibility.textContent = !context.saved ? "Save a filesystem Project to use Story Planner."
      : context.dirty ? "Save the Project before building a Story Plan."
      : context.generations.length > 12 ? "Story Planner supports at most 12 total Generations." : "Build and Apply use the saved Project snapshot.";
  }

  function renderDraft() {
    editor.hidden = !draft;
    cards.replaceChildren(); warnings.replaceChildren();
    if (!draft) return;
    contextInput.value = draft.sequenceContext;
    draft.items.forEach((item, index) => {
      const card = node("fieldset", null, cards); card.className = "story-card"; card.dataset.planItemId = item.planItemId;
      node("legend", `Generation ${index + 1} · ${item.existingGenerationId ? "Existing Generation" : "New Generation"}`, card);
      const selectLabel = node("label", `Select Generation ${index + 1}`, card); selectLabel.className = "story-select";
      const checkbox = node("input", null, selectLabel); checkbox.type = "checkbox"; checkbox.checked = selected.has(item.planItemId);
      checkbox.setAttribute("aria-label", `Select Generation ${index + 1}`);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) selected.add(item.planItemId); else selected.delete(item.planItemId);
        refresh();
      });
      for (const [key, label, limit] of STORY_FIELDS) {
        const input = field(`${item.planItemId}-${key}`, label, key === "relationshipToPrevious" ? "select" : "textarea", card);
        input.dataset.field = key;
        if (key === "relationshipToPrevious") {
          const options = index === 0 ? [["", "No predecessor"]] : RELATIONSHIP_OPTIONS;
          for (const [value, text] of options) { const option = node("option", text, input); option.value = value; }
          input.disabled = index === 0;
        } else { input.rows = key === "creativeRequest" ? 4 : 2; input.maxLength = limit; }
        input.value = item[key] ?? "";
        input.addEventListener(key === "relationshipToPrevious" ? "change" : "input", () => {
          if (busy) return;
          item[key] = key === "relationshipToPrevious" ? input.value || null : input.value;
          // No rerender here: preserve the actual textarea, focus, caret and undo stack.
        });
      }
      const refs = node("div", null, card); refs.className = "story-recommendations";
      for (const ref of item.referenceRecommendations || []) {
        if (!ref || typeof ref.sourceGenerationId !== "string") continue;
        const sourceIndex = draft.items.findIndex(source => source.existingGenerationId && source.existingGenerationId === ref.sourceGenerationId);
        if (sourceIndex < 0 || typeof ref.assetId !== "string" || typeof ref.recommendation !== "string") continue;
        const context = getContext();
        const asset = ref.sourceGenerationId === context.activeGenerationId
          ? context.assets?.find(asset => asset.id === ref.assetId)
          : context.generations.find(g => g.generationId === ref.sourceGenerationId)?.referenceAssets?.find(a => a.id === ref.assetId);
        node("p", `${item.existingGenerationId ? "Recommendation" : "Planned reference"} · Source Generation ${sourceIndex + 1} · ${recommendationAssetLabel(asset, ref.assetId)}. ${item.existingGenerationId ? "Advisory; existing references stay unchanged." : "Will be attached on Apply."} ${ref.recommendation}`, refs);
      }
    });
    for (const warning of draft.consistencyWarnings || []) {
      const index = draft.items.findIndex(item => item.planItemId === warning.planItemId);
      const related = draft.items.findIndex(item => item.planItemId === warning.relatedPlanItemId);
      const label = index === related ? `Generation ${index + 1}`
        : `Generation ${index + 1} → Generation ${related + 1} · ${warning.direction}`;
      node("p", `${label}: ${warning.message}`, warnings);
    }
  }

  function clearDraft() {
    epoch++; draft = null; selected.clear(); busy = null; status.textContent = "";
    scopes.clear(); instruction.value = ""; actionStatus.textContent = "";
    for (const input of scopeContainer.querySelectorAll("input")) input.checked = false;
    renderDraft(); refresh();
  }

  function resetProjectContext() {
    epoch++;
    draft = null; selected.clear(); scopes.clear(); busy = null;
    idea.value = ""; instruction.value = ""; contextInput.value = "";
    for (const input of scopeContainer.querySelectorAll("input")) input.checked = false;
    status.textContent = ""; actionStatus.textContent = "";
    renderDraft(); refresh();
  }

  // One combined prompt protects both canonical edits and the ephemeral proposal.
  function confirmContextChange({ dirty = false, message = "Change Project context?" } = {}) {
    if (busy === "apply") { status.textContent = "Wait for Apply to finish before changing Project context."; return false; }
    if (!draft && !busy && !dirty) return true;
    const details = [draft || busy ? "the Story Planner draft or pending proposal" : "", dirty ? "unsaved Project changes" : ""].filter(Boolean);
    if (!confirmDiscard(`${message} Discard ${details.join(" and ")}?`)) return false;
    clearDraft(); return true;
  }

  async function run(operation, request, kind) {
    const token = ++epoch;
    const projectId = getContext().projectId;
    busy = kind; refresh();
    if (kind === "build") { status.textContent = "Preparing Story Planner proposal…"; actionStatus.textContent = ""; }
    else actionStatus.textContent = kind === "apply" ? "Applying draft…" : "Preparing revision…";
    if (kind === "apply") onApplyBusy(true);
    try {
      const result = await operation(projectId, request);
      if (token !== epoch || projectId !== getContext().projectId) return;
      if (kind === "apply") {
        adoptProject(result); clearDraft(); status.textContent = "Draft applied. The Sequence is saved.";
      } else {
        if (kind === "build") selected.clear();
        draft = structuredClone(result); renderDraft();
        if (kind === "build") status.textContent = "Editable draft only. Review and Apply when ready.";
        else actionStatus.textContent = "Revision applied to the draft. Review the result before Apply.";
      }
    } catch (error) {
      if (token === epoch) {
        if (kind === "build") status.textContent = error.message;
        else actionStatus.textContent = error.message;
      }
    } finally {
      if (kind === "apply") onApplyBusy(false);
      if (token === epoch) { busy = null; refresh(); }
    }
  }

  async function buildDraft() {
    refresh(); if (build.disabled) return;
    if (draft && !confirmDiscard("Replace the existing Story Planner draft?")) return;
    // Retain any old proposal if the new Build fails.
    await run(api.buildStoryPlan, { idea: idea.value, desiredGenerationCount: Number(count.value), llm: plannerSettings() }, "build");
  }
  async function reviseDraft() {
    refresh(); if (revise.disabled) return;
    await run(api.reviseStoryPlan, { draft: structuredClone(draft), selectedPlanItemIds: [...selected],
      scopes: [...scopes], instruction: instruction.value, llm: plannerSettings() }, "revise");
  }
  async function applyDraft() {
    refresh(); if (apply.disabled) return;
    await run(api.applyStoryPlan, { draft: structuredClone(draft) }, "apply");
  }
  contextInput.addEventListener("input", () => { if (draft && !busy) draft.sequenceContext = contextInput.value; });
  idea.addEventListener("input", () => onIdeaChange(idea.value));
  for (const input of [idea, count, instruction, timeout]) input.addEventListener("input", refresh);
  build.addEventListener("click", buildDraft); revise.addEventListener("click", reviseDraft);
  apply.addEventListener("click", applyDraft); discard.addEventListener("click", () => { if (busy !== "apply") clearDraft(); });
  refresh();
  return {
    refresh, confirmContextChange, clearDraft, resetProjectContext,
    get storyIdea() { return idea.value; },
    restoreStoryIdea(value) { idea.value = value ?? ""; refresh(); },
    buildDraft, reviseDraft, applyDraft,
    get hasDraft() { return Boolean(draft || busy); },
  };
}
