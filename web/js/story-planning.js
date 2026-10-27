import { createStoryCreativeDirection } from "./story-creative-direction.js";
import { RELATIONSHIP_OPTIONS, recommendationAssetLabel } from "./planning-workflow.js";
import { createProviderActivity, formatActivityElapsed } from "./provider-activity.js";

export const STORY_FIELDS = [
  ["scenePlan", "Scene Plan", 50000],
  ["dialogue", "Exact Dialogue / Lyrics", 30000],
  ["incomingState", "Incoming State", 20000],
  ["intendedOutgoingState", "Intended Outgoing State", 20000],
  ["relationshipToPrevious", "Relationship to Previous"],
  ["cameraIntent", "Shot Plan / Camera Intent", 20000],
];

export const STORY_PLAN_SCOPES = [
  ["scene_plan", "Scene Plan"],
  ["dialogue", "Exact Dialogue / Lyrics"],
  ["incoming_state", "Incoming State"],
  ["intended_outgoing_state", "Intended Outgoing State"],
  ["relationship_to_previous", "Relationship to Previous"],
  ["camera_intent", "Shot Plan / Camera Intent"],
];

export function storyEligible(context) {
  return context.saved && Boolean(context.projectId) && !context.dirty;
}

export const STORY_NEXT_MAX_GENERATIONS = 4;
export const STORY_INITIAL_RANGE_MIN_GENERATIONS = 2;
export const STORY_INITIAL_RANGE_MAX_GENERATIONS = 12;
export const STORY_REPLAN_MAX_GENERATIONS = 6;
export function storyPlannerSettings(settings, preferences) {
  return {
    ...settings,
    timeoutSeconds: preferences.timeoutSeconds,
    reasoningMode: preferences.reasoning ? "on" : "off",
    storyPlannerVision: preferences.vision === true,
  };
}
export const emptyStoryMemory = () => ({ recentSummary: "", durableFacts: [], openThreads: [] });
export function storyInitialRangeEligible(context, count) {
  return storyEligible(context) && context.generations.length === 1
    && Number.isInteger(count) && count >= STORY_INITIAL_RANGE_MIN_GENERATIONS
    && count <= STORY_INITIAL_RANGE_MAX_GENERATIONS;
}
export function storyNextEligible(context, count) {
  return storyEligible(context) && context.generations.length > 0
    && context.generations.at(-1).storyMemoryStatus === "trusted"
    && context.activeGenerationId === context.generations.at(-1).generationId
    && Number.isInteger(count) && count >= 1 && count <= STORY_NEXT_MAX_GENERATIONS;
}

export function storyInsertEligible(context, count) {
  const index = context.generations.findIndex(generation => generation.generationId === context.activeGenerationId);
  return storyEligible(context) && index >= 0 && index < context.generations.length - 1
    && context.generations[index].storyMemoryStatus === "trusted"
    && Number.isInteger(count) && count >= 1 && count <= STORY_NEXT_MAX_GENERATIONS;
}

export function storyReplanEligible(context, startGenerationId, endGenerationId) {
  const start = context.generations.findIndex(generation => generation.generationId === startGenerationId);
  const end = context.generations.findIndex(generation => generation.generationId === endGenerationId);
  return storyEligible(context) && start >= 0 && end >= start && end - start + 1 <= STORY_REPLAN_MAX_GENERATIONS
    && (start === 0 || context.generations[start - 1].storyMemoryStatus === "trusted");
}

export const storyMemoryStatusLabel = status => ({ trusted: "Trusted", needs_review: "Needs review", legacy_unknown: "Legacy history unknown" }[status] || "Legacy history unknown");
export function projectMemoryStates(source, slots) {
  let current = structuredClone(source);
  return slots.map(slot => {
    const patch = slot.memoryPatchAfter;
    if (!patch || typeof patch !== "object" || Array.isArray(patch)) {
      throw new Error("Story operation item is missing memoryPatchAfter");
    }
    current = structuredClone(current);
    if (patch.recentSummary != null) current.recentSummary = patch.recentSummary;
    for (const [key, upserts, resolved] of [["durableFacts", "factUpserts", "resolvedFactIds"], ["openThreads", "threadUpserts", "resolvedThreadIds"]]) {
      const entries = new Map(current[key].filter(e => !(patch[resolved] || []).includes(e.id)).map(e => [e.id, e]));
      for (const entry of patch[upserts] || []) entries.set(entry.id, structuredClone(entry));
      current[key] = [...entries.values()];
    }
    return current;
  });
}

// Proposals are ephemeral; the Project owns the saved Story idea, planning style, creative direction, and continuity memory.
export function createStoryPlanner({ root, api, getContext, getSettings, adoptProject,
  activity = createProviderActivity(), generateStoryButton = null,
  getPreferences = () => ({ timeoutSeconds: 600, reasoning: true, vision: false }), onPreferencesChange = () => {},
  confirmDiscard = message => globalThis.confirm(message), onApplyBusy = () => {}, onIdeaChange = () => {},
  onCreativeDirectionChange = () => {}, onPlanningStyleChange = () => {}, onMemoryChange = () => {}, onSelectGeneration = () => {}, onConfirmMemory = () => {}, onScenePlanChange = () => {} }) {
  const doc = root.ownerDocument;
  let memoryEdited = false;
  let memory = emptyStoryMemory(), draftOperation = null, transientMemoryIds = new Set();
  let draft = null, selected = new Set(), scopes = new Set(), busy = null, pendingRequest = null, epoch = 0;
  let referencesStale = false;
  let activeController = null, activityToken = null;
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
  const section = (id, title, featureClass = "") => {
    const details = node("details"); details.id = id; details.className = `story-planner-section${featureClass ? ` ${featureClass}` : ""}`;
    details.open = false;
    node("summary", title, details);
    return details;
  };
  const shotGuidanceControl = (key, parent) => {
    const mode = field(`${key}-shot-guidance`, "Shot guidance", "select", parent);
    for (const [value, label] of [["auto", "Auto"], ["prefer", "Prefer"], ["exact", "Exact"]]) {
      const option = node("option", label, mode); option.value = value;
    }
    mode.value = "auto";
    const count = field(`${key}-shots-per-generation`, "Shots per Generation", "input", parent);
    (mode.parentElement || mode.parent).className = "story-shot-guidance-field";
    (count.parentElement || count.parent).className = "story-shot-guidance-field";
    count.type = "number"; count.min = "1"; count.step = "1"; count.value = "";
    const helper = node("p", "Applies independently to each Generation planned by this operation.", parent);
    helper.className = "helper";
    const sync = () => {
      (count.parentElement || count.parent).hidden = mode.value === "auto";
      count.disabled = mode.value === "auto" || Boolean(busy);
    };
    mode.addEventListener("change", sync);
    sync();
    return { mode, count, sync };
  };
  const preferences = getPreferences();
  const executionPreferences = node("div"); executionPreferences.className = "story-planner-preferences";
  const timeout = field("timeout", "Planner request timeout (seconds)", "input", executionPreferences);
  (timeout.parentElement || timeout.parent).className = "story-planner-timeout";
  timeout.type = "number"; timeout.min = "1"; timeout.max = "3600"; timeout.step = "1";
  timeout.value = String(preferences.timeoutSeconds);
  timeout.title = "HTTP client timeout limits waiting. Explicit Cancel aborts MiniConstruct’s provider request. Some model servers may take time to stop inference after the connection closes.";
  const preferenceSwitch = (id, title, checked) => {
    const wrapper = node("div", null, executionPreferences); wrapper.className = "story-planner-preference-item";
    const heading = node("label", title, wrapper); heading.className = "story-planner-preference-title";
    heading.setAttribute("for", id);
    const label = node("label", null, wrapper); label.className = "story-planner-toggle";
    const input = node("input", null, label); input.type = "checkbox"; input.id = id;
    input.className = "story-planner-toggle-input"; input.setAttribute("role", "switch");
    input.setAttribute("aria-label", title); input.checked = checked;
    node("span", "", label).className = "story-planner-toggle-track";
    const state = node("span", checked ? "On" : "Off", label); state.className = "story-planner-toggle-state";
    input.addEventListener("change", () => { state.textContent = input.checked ? "On" : "Off"; savePreferences(); });
    return { input, wrapper };
  };
  const reasoning = preferenceSwitch("story-reasoning", "Story Planner reasoning", preferences.reasoning !== false).input;
  const visionSwitch = preferenceSwitch("story-vision", "Story Planner Vision", preferences.vision === true);
  const vision = visionSwitch.input;
  const visionHelp = node("p", "Sends relevant Picture pixels to Story Planner when the configured model supports vision. Leave off for text and metadata-only planning.", visionSwitch.wrapper);
  visionHelp.className = "helper story-planner-vision-help";
  const savePreferences = () => onPreferencesChange({ timeoutSeconds: Number(timeout.value), reasoning: reasoning.checked, vision: vision.checked });
  vision.addEventListener("change", refresh);
  const buildSection = section("story-build-section", "Build Initial Range", "story-build-section");
  const buildHelper = node("p", "Recommended: plan the saved Generation 1 plus 1–3 new Generations. Choose 5–12 for an Extended single-request build.", buildSection);
  buildHelper.className = "helper";
  const idea = field("idea", "Story Plan / Premise", "textarea", buildSection);
  idea.placeholder = "Describe the Sequence using Narrative Names or technical Subject N labels.";
  node("p", "Save the Story Plan / Premise before Build Initial Range. Story Planner develops it into Scene Plans for each Generation.", buildSection).className = "helper";
  idea.rows = 4; idea.maxLength = 20000;
  const count = field("count", "Initial Generations (2–4 recommended; 5–12 extended)", "input", buildSection);
  count.type = "number"; count.min = "2"; count.max = "12"; count.step = "1"; count.value = "4";
  const buildShots = shotGuidanceControl("build", buildSection);
  const extendedBuildWarning = node("p", "Extended builds use a larger single model request and may have weaker long-range consistency. Larger builds may need a higher Max output tokens setting and longer Story Planner timeout, especially with reasoning enabled. For tighter planning, use 2–4 and continue with Plan Next.", buildSection);
  extendedBuildWarning.id = "story-extended-build-warning"; extendedBuildWarning.className = "warning-banner"; extendedBuildWarning.hidden = true;
  const build = button("build", "Build Initial Range", buildSection); build.className = "primary";
  const memorySection = section("story-memory", "Story Continuity Memory", "story-memory-panel");
  const memoryTitle = node("h3", "", memorySection); memoryTitle.id = "story-memory-title";
  const memoryTrust = node("p", "", memorySection); memoryTrust.id = "story-memory-trust"; memoryTrust.className = "helper story-memory-trust";
  const confirmMemory = button("confirm-memory", "Confirm Story Memory", memorySection);
  confirmMemory.addEventListener("click", async () => {
    if (confirmMemory.disabled) return;
    memoryEdited = true; onMemoryChange(memoryPayload());
    await onConfirmMemory(getContext().activeGenerationId);
  });
  const timeline = node("details", null, memorySection); timeline.id = "story-memory-timeline";
  node("summary", "Memory Timeline", timeline);
  const timelineRows = node("div", null, timeline); timelineRows.className = "story-memory-timeline-rows";
  const summary = field("memory-summary", "Story Summary", "textarea", memorySection);
  summary.rows = 3; summary.maxLength = 6000;
  const memoryLists = node("div", null, memorySection); memoryLists.className = "story-memory-lists";
  function memoryPayload() {
    const payload = structuredClone(memory);
    for (const key of ["durableFacts", "openThreads"]) {
      payload[key] = payload[key].filter(entry => entry.text.trim());
    }
    return payload;
  }
  function memoryChanged() { memoryEdited = true; onMemoryChange(memoryPayload()); refresh(); }
  summary.addEventListener("input", () => { memory.recentSummary = summary.value; memoryChanged(); });
  function renderMemory() {
    summary.value = memory.recentSummary; memoryLists.replaceChildren();
    for (const [key, label] of [["durableFacts", "Durable Facts"], ["openThreads", "Open Threads"]]) {
      const group = node("section", null, memoryLists); group.className = "story-memory-group";
      const groupHeading = node("h3", label, group); groupHeading.className = "story-memory-group-heading";
      const entries = memory[key];
      let finalEntryActions = null;
      entries.forEach((entry, index) => {
        const row = node("div", null, group); row.className = "story-memory-entry";
        const text = field(`memory-${entry.id}`, key === "durableFacts" ? "Fact" : "Thread", "textarea", row);
        text.value = entry.text; text.maxLength = 500; text.rows = 2;
        text.addEventListener("input", () => {
          if (!text.value.trim() && !transientMemoryIds.has(entry.id)) {
            memory[key] = memory[key].filter(candidate => candidate.id !== entry.id);
            renderMemory();
          } else {
            entry.text = text.value;
          }
          memoryChanged();
        });
        if (entry.relatedSubjectIds?.length) {
          const cast = getContext().subjects || [];
          node("p", "Related Subjects: " + entry.relatedSubjectIds.map(id => {
            const subject = cast.find(s => s.id === id);
            return subject?.narrativeName || (subject ? `Subject ${subject.number}` : id);
          }).join(", "), row);
        }
        const entryActions = node("div", null, row);
        entryActions.className = `story-memory-action-row story-memory-entry-actions${index === entries.length - 1 ? " story-memory-group-actions" : ""}`;
        if (index === entries.length - 1) finalEntryActions = entryActions;
        const remove = button(`remove-${entry.id}`, key === "openThreads" ? "Resolve / remove" : "Remove", entryActions);
        remove.addEventListener("click", () => {
          if (busy) return;
          memory[key] = memory[key].filter(e => e.id !== entry.id);
          transientMemoryIds.delete(entry.id);
          renderMemory(); memoryChanged();
        });
      });
      const groupActions = finalEntryActions || node("div", null, group);
      if (!finalEntryActions) groupActions.className = "story-memory-action-row story-memory-group-actions";
      const add = button(`add-${key}`, key === "durableFacts" ? "Add fact" : "Add thread", groupActions);
      add.disabled = memory[key].length >= 64;
      add.addEventListener("click", () => {
        if (busy || memory[key].length >= 64) return;
        const id = globalThis.crypto.randomUUID();
        memory[key].push({ id, text: "", relatedSubjectIds: [] });
        transientMemoryIds.add(id);
        renderMemory(); memoryChanged();
      });
    }
  }
  renderMemory();
  const creativeDirectionSection = section("story-creative-direction", "Creative Direction", "story-creative-direction-section");
  node("p", "Soft Story-planning presets, separate from Story Memory and H3 controls. Auto adds nothing; Custom accepts prose.", creativeDirectionSection).className = "helper";
  const creativeDirection = createStoryCreativeDirection({ root: creativeDirectionSection,
    confirmReplace: confirmDiscard, onChange: onCreativeDirectionChange });
  const planningStyleSection = section("story-planning-style", "Planning Style", "story-planning-style-section");
  const planningStyleOptions = {
    creativity: [["faithful", "Faithful"], ["balanced", "Balanced"], ["exploratory", "Exploratory"]],
    scenePlanDetail: [["concise", "Concise"], ["standard", "Standard"], ["detailed", "Detailed"]],
  };
  const planningStyleValues = { creativity: "balanced", scenePlanDetail: "standard" };
  const planningStyleControls = {};
  function renderPlanningStyleGroup(key, label) {
    const group = node("div", null, planningStyleSection);
    group.className = "creative-interpretation-control";
    node("label", label, group);
    const controls = node("div", null, group);
    controls.id = `story-${key}-controls`;
    controls.className = "segmented";
    controls.setAttribute("role", "group");
    controls.setAttribute("aria-label", label);
    planningStyleControls[key] = controls;
    for (const [value, text] of planningStyleOptions[key]) {
      const choice = button(`${key}-${value}`, text, controls);
      choice.className = "control-choice";
      choice.dataset.planningStyle = key;
      choice.dataset.value = value;
      choice.setAttribute("aria-pressed", "false");
      choice.addEventListener("click", () => {
        if (planningStyleValues[key] === value) return;
        planningStyleValues[key] = value;
        renderPlanningStyle();
        onPlanningStyleChange();
      });
    }
  }
  function renderPlanningStyle() {
    for (const key of Object.keys(planningStyleOptions)) {
      for (const choice of planningStyleControls[key].children) {
        const selected = planningStyleValues[key] === choice.dataset.value;
        choice.className = `control-choice${selected ? " active" : ""}`;
        choice.setAttribute("aria-pressed", String(selected));
      }
    }
  }
  renderPlanningStyleGroup("creativity", "Planning Creativity");
  renderPlanningStyleGroup("scenePlanDetail", "Scene Plan Detail");
  node("p", "Creativity sets local invention latitude. Detail sets how concretely the chosen development is described; it does not change beat density or shot count.", planningStyleSection).className = "helper";
  const resetPlanningStyle = button("reset-planning-style", "Reset Planning Style", planningStyleSection);
  const restorePlanningStyle = value => {
    planningStyleValues.creativity = value?.creativity || "balanced";
    planningStyleValues.scenePlanDetail = value?.scenePlanDetail || "standard";
    renderPlanningStyle();
  };
  restorePlanningStyle();
  resetPlanningStyle.addEventListener("click", () => {
    if (planningStyleValues.creativity === "balanced" && planningStyleValues.scenePlanDetail === "standard") return;
    restorePlanningStyle(); onPlanningStyleChange();
  });
  const nextSection = section("story-next-section", "Plan Next", "story-next-section");
  const anchorLabel = node("p", "", nextSection); anchorLabel.id = "story-next-anchor";
  const nextCount = field("next-count", "New Generations (1–4)", "input", nextSection);
  nextCount.type = "number"; nextCount.min = "1"; nextCount.max = "4"; nextCount.step = "1"; nextCount.value = "3";
  const nextShots = shotGuidanceControl("next", nextSection);
  const nextInstruction = field("next-instruction", "Plan Next instruction (optional)", "textarea", nextSection);
  nextInstruction.rows = 2; nextInstruction.maxLength = 8000;
  const next = button("next", "Plan Next", nextSection); next.className = "primary";

  const insertSection = section("story-insert-section", "Insert Between", "story-insert-section");
  const insertLabel = node("p", "", insertSection); insertLabel.id = "story-insert-boundary"; insertLabel.className = "helper";
  const insertCount = field("insert-count", "New Generations (1–4)", "input", insertSection);
  insertCount.type = "number"; insertCount.min = "1"; insertCount.max = "4"; insertCount.step = "1"; insertCount.value = "1";
  const insertShots = shotGuidanceControl("insert", insertSection);
  const insertInstruction = field("insert-instruction", "Insert instruction (optional)", "textarea", insertSection);
  insertInstruction.rows = 2; insertInstruction.maxLength = 8000;
  const insert = button("insert", "Plan Insert", insertSection); insert.className = "primary";

  const replanSection = section("story-replan-section", "Replan Range", "story-replan-section");
  const replanLabel = node("p", "", replanSection); replanLabel.id = "story-replan-boundary"; replanLabel.className = "helper";
  const replanStart = field("replan-start", "Start Generation", "select", replanSection);
  const replanEnd = field("replan-end", "End Generation", "select", replanSection);
  const replanShots = shotGuidanceControl("replan", replanSection);
  const replanInstruction = field("replan-instruction", "Replan instruction (optional)", "textarea", replanSection);
  replanInstruction.rows = 2; replanInstruction.maxLength = 8000;
  const replan = button("replan", "Plan Range", replanSection); replan.className = "primary";

  const scenePlanSection = node("section"); scenePlanSection.id = "story-current-scene-plan";
  const appliedScenePlan = field("applied-scene-plan", "Scene Plan — Current Generation", "textarea", scenePlanSection);
  appliedScenePlan.rows = 6; appliedScenePlan.maxLength = 50000;
  node("p", "Edit the saved Scene Plan for the selected Generation, then save the Project. Story-aware H3 generation uses the saved Scene Plan. Later Generations change only through explicit Story operations such as Replan Range.", scenePlanSection).className = "helper";
  if (generateStoryButton) {
    scenePlanSection.append(generateStoryButton);
    const feedback = node("p", "", scenePlanSection); feedback.id = "story-generation-feedback";
    feedback.className = "helper"; feedback.setAttribute("role", "status");
  }
  appliedScenePlan.addEventListener("input", () => onScenePlanChange(appliedScenePlan.value));

  const plannerSettings = () => storyPlannerSettings(getSettings(), { timeoutSeconds: Number(timeout.value), reasoning: reasoning.checked, vision: vision.checked });
  const eligibility = node("p", ""); eligibility.className = "helper";
  const status = node("p", ""); status.id = "story-status"; status.className = "helper";
  status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
  const running = node("div"); running.id = "story-running"; running.hidden = true;
  running.setAttribute("role", "status");
  const runningText = node("span", "", running);
  const cancel = button("cancel", "Cancel", running);
  const operationNames = { "initial-range": "Build Initial Range", next: "Plan Next", insert: "Insert Between", replan: "Replan Range", revise: "Story Revision" };
  function stopRunning() {
    activity.finish(activityToken); activityToken = null; activeController = null;
  }
  function cancelRequest() {
    if (!activeController || !busy) return;
    activeController.abort(); epoch++; busy = null; pendingRequest = null;
    stopRunning(); status.textContent = "Story Planner request cancelled. You can continue working."; refresh();
  }
  cancel.addEventListener("click", () => activity.cancelActive());
  const rawResponseAction = button("view-raw-response", "View raw response");
  rawResponseAction.className = "helper"; rawResponseAction.hidden = true;
  const rawResponseDialog = node("dialog"); rawResponseDialog.id = "story-raw-response-dialog";
  const rawResponseHeading = node("h2", "Rejected Story Planner response", rawResponseDialog);
  rawResponseHeading.id = "story-raw-response-heading";
  rawResponseDialog.setAttribute("aria-labelledby", rawResponseHeading.id);
  node("p", "This response was rejected and was NOT applied.", rawResponseDialog);
  const rawResponseText = node("pre", "", rawResponseDialog); rawResponseText.id = "story-raw-response-text";
  const rawResponseCopyStatus = node("p", "", rawResponseDialog); rawResponseCopyStatus.setAttribute("role", "status");
  const rawResponseActions = node("div", null, rawResponseDialog);
  const copyRawResponse = button("copy-raw-response", "Copy", rawResponseActions);
  const closeRawResponse = button("close-raw-response", "Close", rawResponseActions);
  let failedRawResponse = null;
  const clearFailedRawResponse = () => {
    failedRawResponse = null; rawResponseText.textContent = "";
    rawResponseCopyStatus.textContent = ""; rawResponseAction.hidden = true;
  };
  rawResponseAction.addEventListener("click", () => {
    rawResponseText.textContent = failedRawResponse ?? "";
    rawResponseDialog.showModal?.();
  });
  copyRawResponse.addEventListener("click", async () => {
    if (failedRawResponse == null) return;
    try {
      const clipboard = globalThis.navigator?.clipboard;
      if (typeof clipboard?.writeText !== "function") throw new Error("clipboard unavailable");
      await clipboard.writeText(failedRawResponse);
      rawResponseCopyStatus.textContent = "Raw response copied.";
    } catch {
      rawResponseCopyStatus.textContent = "Clipboard access is unavailable in this browser.";
    }
  });
  closeRawResponse.addEventListener("click", () => rawResponseDialog.close?.());
  const editor = node("div"); editor.className = "story-editor"; editor.hidden = true;
  const contextInput = field("context", "Draft Sequence Context", "textarea", editor);
  contextInput.rows = 3; contextInput.maxLength = 20000;
  const cards = node("div", null, editor); cards.className = "story-cards";
  const warnings = node("div", null, editor); warnings.id = "story-warnings";
  const dialogueHelp = node("p", "Exact Dialogue / Lyrics is verbatim text. Leave it blank for no exact dialogue requirement. Select it explicitly to revise it; every unselected field stays exact.", editor);
  dialogueHelp.className = "helper";
  const scopeGroup = node("section", null, editor); scopeGroup.className = "story-scope-group";
  const scopeHeading = node("h3", "Fields to revise", scopeGroup); scopeHeading.className = "story-subheading";
  scopeHeading.id = "story-scope-heading";
  scopeGroup.setAttribute("role", "group"); scopeGroup.setAttribute("aria-labelledby", scopeHeading.id);
  node("p", "Choose which fields the model may change; unselected fields stay exact.", scopeGroup).className = "helper";
  const scopeContainer = node("div", null, scopeGroup); scopeContainer.className = "story-scopes";
  for (const [key, label] of STORY_PLAN_SCOPES) {
    const option = node("label", null, scopeContainer); option.className = "story-scope-option";
    const input = node("input", null, option); input.type = "checkbox"; input.id = `story-scope-${key}`;
    node("span", label, option);
    input.addEventListener("change", () => {
      if (input.checked) scopes.add(key); else scopes.delete(key);
      refresh();
    });
  }
  const instruction = field("instruction", "Revision instruction", "textarea", editor);
  const revisionInstructionField = instruction.parentNode;
  instruction.rows = 3; instruction.maxLength = 8000;
  const actionStatus = node("p", "", editor); actionStatus.id = "story-action-status";
  actionStatus.className = "helper story-action-status"; actionStatus.setAttribute("role", "status"); actionStatus.setAttribute("aria-live", "polite");
  const revisionActions = node("div", null, editor); revisionActions.className = "story-action-group story-revision-actions";
  const revise = button("revise", "Revise Selected", revisionActions); revise.className = "primary";
  const applyActions = node("div", null, editor); applyActions.className = "story-action-group story-apply-actions";
  const apply = button("apply", "Apply Draft", applyActions); apply.className = "primary";
  const discard = button("discard", "Discard Draft", applyActions);

  const draftOperations = {
    "initial-range": { section: buildSection, items: "items", apply: "applyInitialRange" },
    next: { section: nextSection, items: "slots", apply: "applyStoryNext" },
    insert: { section: insertSection, items: "slots", apply: "applyStoryInsert" },
    replan: { section: replanSection, items: "items", apply: "applyStoryReplan" },
  };
  const isBoundedDraft = () => draftOperation in draftOperations;
  activity.subscribe(active => {
    running.hidden = !active || active.owner !== "story";
    if (running.hidden) return;
    const target = draftOperations[busy]?.section || revisionActions;
    if ((running.parentNode || running.parentElement || running.parent) !== target) {
      const current = running.parentNode || running.parentElement || running.parent;
      if (current?.removeChild) current.removeChild(running);
      else if (current?.children) current.children = current.children.filter(child => child !== running);
      target.append(running);
      target.open = true;
    }
    runningText.textContent = `${active.cancelling ? "Cancelling" : "Generating"} — ${active.label} · ${formatActivityElapsed(activity.elapsed(active))} `;
  });
  function draftStartOrdinal() {
    if (draftOperation === "next") return draft.authority.anchorOrdinal + 1;
    if (draftOperation === "insert") return draft.authority.upstreamOrdinal + 1;
    if (draftOperation === "replan") return draft.authority.startOrdinal;
    return 1;
  }

  // Keep the ephemeral editor beside the operation that produced it. This is
  // especially important for Plan Next, whose draft cards are its continuation.
  function placeEditor(parent) {
    const current = editor.parentNode || editor.parent;
    if (current === parent) return;
    if (current?.removeChild) current.removeChild(editor);
    else if (current?.children) current.children = current.children.filter(child => child !== editor);
    parent.append(editor);
  }

  function syncReplanSelectors(context) {
    const priorStart = replanStart.value;
    replanStart.replaceChildren();
    context.generations.forEach((generation, index) => {
      const option = node("option", `Generation ${index + 1}`, replanStart);
      option.value = generation.generationId;
    });
    const startExists = context.generations.some(generation => generation.generationId === priorStart);
    replanStart.value = startExists ? priorStart : (context.generations.some(generation => generation.generationId === context.activeGenerationId)
      ? context.activeGenerationId : context.generations[0]?.generationId || "");
    const startIndex = context.generations.findIndex(generation => generation.generationId === replanStart.value);
    const priorEnd = replanEnd.value;
    replanEnd.replaceChildren();
    const last = Math.min(context.generations.length - 1, startIndex + STORY_REPLAN_MAX_GENERATIONS - 1);
    for (let index = Math.max(0, startIndex); index <= last; index++) {
      const option = node("option", `Generation ${index + 1}`, replanEnd);
      option.value = context.generations[index].generationId;
    }
    const endAllowed = context.generations.slice(Math.max(0, startIndex), last + 1)
      .some(generation => generation.generationId === priorEnd);
    replanEnd.value = endAllowed ? priorEnd : replanStart.value;
  }

  function refresh() {
    const context = getContext();
    const eligible = storyEligible(context);
    const activeIndex = context.generations.findIndex(g => g.generationId === context.activeGenerationId);
    const active = context.generations[activeIndex];
    const total = Number(count.value);
    memoryTitle.textContent = `Story Memory After Generation ${activeIndex < 0 ? 1 : activeIndex + 1}`;
    memoryTrust.dataset.status = active?.storyMemoryStatus || "trusted";
    memoryTrust.textContent = storyMemoryStatusLabel(active?.storyMemoryStatus || (context.saved ? "legacy_unknown" : "trusted"))
      + (active?.storyMemoryStatus === "needs_review" ? ". An earlier change may have made this saved state stale. Review, then edit and Save or confirm it."
        : active?.storyMemoryStatus === "legacy_unknown" ? ". This older Project never stored this historical state. Empty fields do not mean the story was empty; fill them and Save." : "");
    confirmMemory.hidden = !context.saved || !active || active.storyMemoryStatus === "trusted";
    confirmMemory.disabled = Boolean(busy) || !eligible;
    timeline.hidden = !context.saved;
    timelineRows.replaceChildren();
    context.generations.forEach((g, index) => {
      const row = button(`memory-timeline-${g.generationId}`, `Generation ${index + 1} · ${storyMemoryStatusLabel(g.storyMemoryStatus)}${g.generationId === context.activeGenerationId ? " · selected" : ""}`, timelineRows);
      row.disabled = Boolean(busy);
      row.setAttribute("aria-current", g.generationId === context.activeGenerationId ? "true" : "false");
      row.addEventListener("click", () => onSelectGeneration(g.generationId));
    });
    anchorLabel.textContent = `Terminal anchor: Generation ${context.generations.length}. `
      + (context.activeGenerationId === context.generations.at(-1)?.generationId
        ? "The anchor stays unchanged." : "Select the last Generation to Plan Next.");
    if (context.generations.at(-1)?.storyMemoryStatus !== "trusted") anchorLabel.textContent += " Review and confirm the terminal Generation Story Memory before Plan Next.";
    const downstream = activeIndex >= 0 ? context.generations[activeIndex + 1] : null;
    if (active && downstream) {
      insertLabel.textContent = `Insert after: Generation ${activeIndex + 1}. Protected next Generation: Generation ${activeIndex + 2}. `
        + (active.storyMemoryStatus === "trusted" ? "Generation A and Generation B remain read-only boundaries."
          : `Insert Between is blocked because Generation ${activeIndex + 1} Story Memory is ${storyMemoryStatusLabel(active.storyMemoryStatus).toLowerCase()}. Review and confirm it first.`);
    } else if (active) {
      insertLabel.textContent = "Insert Between requires a nonterminal selected Generation with a protected next Generation.";
    } else {
      insertLabel.textContent = "Save a filesystem Project and select a nonterminal Generation to Insert Between.";
    }
    syncReplanSelectors(context);
    const replanStartIndex = context.generations.findIndex(generation => generation.generationId === replanStart.value);
    const replanEndIndex = context.generations.findIndex(generation => generation.generationId === replanEnd.value);
    const replanUpstream = replanStartIndex > 0 ? context.generations[replanStartIndex - 1] : null;
    const replanDownstream = replanEndIndex >= 0 && replanEndIndex + 1 < context.generations.length
      ? context.generations[replanEndIndex + 1] : null;
    if (replanStartIndex < 0 || replanEndIndex < 0) {
      replanLabel.textContent = "Save a filesystem Project with Generations to replan a range.";
    } else if (replanUpstream && replanUpstream.storyMemoryStatus !== "trusted") {
      replanLabel.textContent = `Replan is blocked because protected upstream Generation ${replanStartIndex} Story Memory is ${storyMemoryStatusLabel(replanUpstream.storyMemoryStatus).toLowerCase()}. Review and confirm it first.`;
    } else {
      replanLabel.textContent = `Rewrite existing Generations ${replanStartIndex + 1}–${replanEndIndex + 1} (1–${STORY_REPLAN_MAX_GENERATIONS}). `
        + `${replanUpstream ? `Protected upstream: Generation ${replanStartIndex}. ` : "This range begins at Generation 1. "}`
        + `${replanDownstream ? `Protected downstream: Generation ${replanEndIndex + 2}.` : "This range ends at the terminal Generation."}`;
    }
    count.min = String(STORY_INITIAL_RANGE_MIN_GENERATIONS);
    count.max = String(STORY_INITIAL_RANGE_MAX_GENERATIONS);
    extendedBuildWarning.hidden = !(total > 4 && total <= STORY_INITIAL_RANGE_MAX_GENERATIONS);
    buildHelper.textContent = context.generations.length !== 1
      ? "Initial Range is only for a Project with one saved Generation. Use Replan Range to rewrite existing Story Generations, or Plan Next to continue."
      : "Recommended: plan the saved Generation 1 plus 1–3 new Generations. Choose 5–12 for an Extended single-request build.";
    discard.disabled = busy === "apply";
    creativeDirection.setBusy(Boolean(busy));
    for (const controls of Object.values(planningStyleControls)) {
      for (const choice of controls.children) choice.disabled = Boolean(busy);
    }
    resetPlanningStyle.disabled = Boolean(busy);
    for (const input of root.querySelectorAll("textarea, input, select")) input.disabled = Boolean(busy);
    for (const control of [buildShots, nextShots, insertShots, replanShots]) control.sync();
    const modelAcceptsImages = getSettings().supportsVision === true;
    visionHelp.textContent = vision.checked && !modelAcceptsImages
      ? 'Story Planner Vision is enabled, but inactive until "Model accepts images" is enabled in Settings.'
      : "Sends relevant Picture pixels to Story Planner when enabled. Leave off for text and metadata-only planning.";
    // A signed Replan proposal owns its selected C..D boundaries. Keep the
    // displayed selectors aligned with that proposal until Apply or Discard.
    if (draftOperation === "replan") {
      replanStart.disabled = true;
      replanEnd.disabled = true;
    }
    for (const control of memoryLists.querySelectorAll("button")) {
      const key = control.id?.replace("story-add-", "");
      control.disabled = Boolean(busy) || (Array.isArray(memory[key]) && memory[key].length >= 64);
    }
    // First Generation never has a predecessor.
    const firstRelationship = cards.querySelector("select");
    if (firstRelationship && draftStartOrdinal() === 1) firstRelationship.disabled = true;
    eligibility.textContent = !context.saved ? "Save a filesystem Project to use Story Planner."
      : context.dirty ? "Save the Project before building a Story Plan."
      : context.generations.length !== 1 ? "Build Initial Range is complete for this Project. Plan Next appends 1–4 Generations." : "Build Initial Range and Apply use the saved Project snapshot.";
  }

  function renderDraft() {
    editor.hidden = !draft;
    const draftParent = draftOperations[draftOperation]?.section || buildSection;
    placeEditor(draftParent);
    if (draft) draftParent.open = true;
    cards.replaceChildren(); warnings.replaceChildren();
    if (!draft) return;
    const operationDraft = isBoundedDraft();
    if (contextInput.parentNode) contextInput.parentNode.hidden = operationDraft;
    contextInput.hidden = operationDraft;
    scopeGroup.hidden = operationDraft; revisionActions.hidden = operationDraft;
    revisionInstructionField.hidden = operationDraft;
    dialogueHelp.hidden = operationDraft;
    contextInput.value = draft.sequenceContext || "";
    const operationItems = draft[draftOperations[draftOperation]?.items || "items"];
    for (const item of operationItems) delete item.tonePerformance;
    const projected = operationDraft ? projectMemoryStates(draftOperation === "initial-range" ? emptyStoryMemory() : draftOperation === "replan" ? draft.authority.memoryBeforeRange : memory, operationItems) : [];
    if (operationDraft) for (const warning of draft.warnings || []) node("p", warning, warnings);
    operationItems.forEach((item, index) => {
      const card = node("fieldset", null, cards); card.className = "story-card"; card.dataset.planItemId = item.planItemId || item.existingGenerationId;
      const ordinal = draftStartOrdinal() + index;
      node("legend", `Generation ${ordinal} · ${item.existingGenerationId ? "Existing Generation" : "New Generation"}`, card);
      const selectLabel = node("label", `Select Generation ${index + 1}`, card); selectLabel.className = "story-select"; selectLabel.hidden = operationDraft;
      const checkbox = node("input", null, selectLabel); checkbox.type = "checkbox"; checkbox.checked = selected.has(item.planItemId);
      checkbox.setAttribute("aria-label", `Select Generation ${index + 1}`);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) selected.add(item.planItemId); else selected.delete(item.planItemId);
        refresh();
      });
      const technical = node("details", null, card); technical.className = "story-technical-details";
      node("summary", "Planning details", technical);
      const shots = field(`${item.planItemId || item.existingGenerationId}-shotCount`, "Shots", "input", card);
      shots.dataset.field = "shotCount"; shots.type = "number"; shots.min = "1"; shots.step = "1";
      shots.value = String(item.shotCount ?? 1);
      const shotReview = node("p", "Review Shot Plan / Camera Intent after changing shot count.", card);
      shotReview.className = "helper"; shotReview.hidden = true;
      shots.addEventListener("input", () => {
        if (busy) return;
        item.shotCount = Number(shots.value);
        shotReview.hidden = false;
      });
      node("p", "Story Planner may use one or multiple shots. Incoming State anchors the first shot; Intended Outgoing State anchors the final shot.", card).className = "helper";
      for (const [key, label, limit] of STORY_FIELDS) {
        const itemId = item.planItemId || item.existingGenerationId;
        const prominent = ["scenePlan", "relationshipToPrevious", "cameraIntent"].includes(key);
        const input = field(`${itemId}-${key}`, label, key === "relationshipToPrevious" ? "select" : "textarea", prominent ? card : technical);
        input.dataset.field = key;
        if (key === "relationshipToPrevious") {
          const options = index === 0 && !operationDraft ? [["", "No predecessor"]] : RELATIONSHIP_OPTIONS;
          for (const [value, text] of options) { const option = node("option", text, input); option.value = value; }
          input.disabled = ordinal === 1;
        } else { input.rows = key === "scenePlan" ? 6 : key === "dialogue" ? 3 : 2; input.maxLength = limit; }
        input.value = item[key] ?? "";
        input.addEventListener(key === "relationshipToPrevious" ? "change" : "input", () => {
          if (busy) return;
          item[key] = key === "relationshipToPrevious" ? input.value || null : input.value;
          // No rerender here: preserve the actual textarea, focus, caret and undo stack.
        });
      }
      if (!item.existingGenerationId && operationDraft) {
        const duration = field(`${item.planItemId}-duration`, "Duration (seconds; blank uses Generation 1)", "input", card);
        duration.type = "number"; duration.min = "4"; duration.max = "15"; duration.step = "1";
        duration.value = item.durationSeconds ?? "";
        duration.addEventListener("input", () => { item.durationSeconds = duration.value === "" ? null : Number(duration.value); });
      }
      if (!item.existingGenerationId) {
        const participation = node("details", null, card); participation.className = "story-subject-participation";
        node("summary", "Subjects in this Generation", participation);
        node("p", "Select only characters physically or visually appearing in this shot. Established identity references are carried automatically when available.", participation).className = "helper";
        item.participatingSubjectIds = Array.isArray(item.participatingSubjectIds) ? item.participatingSubjectIds : [];
        const canonicalSubjects = Array.isArray(getContext().subjects) ? getContext().subjects : [];
        const allowedIds = new Set(draft.authority?.allowedSubjectIds || canonicalSubjects.map(subject => subject.id));
        const cast = canonicalSubjects.filter(subject => allowedIds.has(subject.id));
        if (!cast.length) node("p", "No canonical Subjects are available for this proposal.", participation).className = "helper";
        for (const subject of cast) {
          const displayName = subject.narrativeName ? `${subject.narrativeName} (Subject ${subject.number})` : `Subject ${subject.number}`;
          const label = node("label", displayName, participation);
          const input = node("input", null, label); input.type = "checkbox"; input.checked = item.participatingSubjectIds.includes(subject.id);
          input.addEventListener("change", () => {
            if (busy) return;
            item.participatingSubjectIds = cast.filter(candidate => {
              const candidateInput = [...participation.querySelectorAll("input")]
                .find(element => element.dataset.subjectId === candidate.id);
              return candidateInput?.checked;
            }).map(candidate => candidate.id);
          });
          input.dataset.subjectId = subject.id;
        }
      }
      if (operationDraft) {
        const details = node("details", null, card); details.className = "story-draft-memory";
        node("summary", "Story Memory After This Generation", details);
        node("p", "Projected state for review. Memory changes are read-only in this draft.", details).className = "helper";
        node("h3", "Story Summary", details); node("p", projected[index].recentSummary || "(empty)", details);
        for (const [key, label] of [["durableFacts", "Durable Facts"], ["openThreads", "Open Threads"]]) {
          node("h3", label, details);
          if (!projected[index][key].length) node("p", "None", details);
          for (const entry of projected[index][key]) node("p", entry.text, details);
        }
      }
      const refs = node("div", null, card); refs.className = "story-recommendations";
      for (const ref of item.referenceRecommendations || []) {
        if (!ref || typeof ref.sourceGenerationId !== "string") continue;
        const sourceIndex = operationDraft ? getContext().generations.findIndex(source => source.generationId === ref.sourceGenerationId)
          : draft.items.findIndex(source => source.existingGenerationId === ref.sourceGenerationId);
        if (sourceIndex < 0 || typeof ref.assetId !== "string" || typeof ref.recommendation !== "string") continue;
        const context = getContext();
        const asset = ref.sourceGenerationId === context.activeGenerationId
          ? context.assets?.find(asset => asset.id === ref.assetId)
          : context.generations.find(g => g.generationId === ref.sourceGenerationId)?.referenceAssets?.find(a => a.id === ref.assetId);
        node("p", `${item.existingGenerationId ? "Recommendation" : "Planned reference"} · Source Generation ${sourceIndex + 1} · ${recommendationAssetLabel(asset, ref.assetId)}. ${item.existingGenerationId ? "Advisory; existing references stay unchanged." : "Will be attached on Apply."} ${ref.recommendation}${item.existingGenerationId ? "" : " (Optional model-recommended evidence; automatic identity and same-scene environment continuity are separate.)"}`, refs);
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

  // Explicit entry point for legacy ordinary drafts; bounded operations never
  // infer a different owner or relax their memory-patch contract from response shape.
  function restoreRevisionDraft(value) {
    if (!confirmContextChange({ message: "Load an ordinary Story draft?" })) return false;
    clearDraft();
    draft = structuredClone(value); draftOperation = "revision";
    renderDraft(); refresh(); return true;
  }

  function clearDraft({ clearNextInstruction = false, clearInsertInstruction = false, clearReplanInstruction = false } = {}) {
    referencesStale = false;
    activeController?.abort(); stopRunning();
    epoch++; draft = null; draftOperation = null; selected.clear(); busy = null; pendingRequest = null; status.textContent = "";
    clearFailedRawResponse();
    if (clearNextInstruction) nextInstruction.value = "";
    if (clearInsertInstruction) insertInstruction.value = "";
    if (clearReplanInstruction) replanInstruction.value = "";
    scopes.clear(); instruction.value = ""; actionStatus.textContent = "";
    for (const input of scopeContainer.querySelectorAll("input")) input.checked = false;
    renderDraft(); refresh();
  }

  function resetProjectContext() {
    referencesStale = false;
    activeController?.abort(); stopRunning();
    epoch++;
    draft = null; draftOperation = null; memory = emptyStoryMemory(); memoryEdited = false; transientMemoryIds.clear(); renderMemory(); selected.clear(); scopes.clear(); busy = null; pendingRequest = null;
    idea.value = ""; instruction.value = ""; nextInstruction.value = ""; insertInstruction.value = ""; replanInstruction.value = ""; contextInput.value = "";
    for (const control of [buildShots, nextShots, insertShots, replanShots]) {
      control.mode.value = "auto"; control.count.value = ""; control.sync();
    }
    creativeDirection.restore();
    restorePlanningStyle();
    for (const input of scopeContainer.querySelectorAll("input")) input.checked = false;
    status.textContent = ""; actionStatus.textContent = "";
    clearFailedRawResponse();
    renderDraft(); refresh();
  }

  // One combined prompt protects both canonical edits and the ephemeral proposal.
  function confirmContextChange({ dirty = false, message = "Change Project context?", allowPendingSelection = false } = {}) {
    if (busy === "apply") { status.textContent = "Wait for Apply to finish before changing Project context."; return false; }
    if (busy && allowPendingSelection && !dirty && busy in draftOperations) return true;
    if (busy) { status.textContent = "Wait for the Story proposal request to finish before changing saved Story authority. You can discard the pending request first."; return false; }
    if (!draft && !busy && !dirty) return true;
    const details = [draft || busy ? "the Story Planner draft or pending proposal" : "", dirty ? "unsaved Project changes" : ""].filter(Boolean);
    if (!confirmDiscard(`${message} Discard ${details.join(" and ")}?`)) return false;
    clearDraft(); return true;
  }

  async function run(operation, request, kind) {
    const token = ++epoch;
    const projectId = getContext().projectId;
    if (kind in draftOperations || kind === "revise") clearFailedRawResponse();
    busy = kind; pendingRequest = kind in draftOperations || kind === "revise" ? { kind, request } : null;
    const providerRequest = kind !== "apply";
    const controller = providerRequest ? new AbortController() : null;
    if (controller) {
      activeController = controller;
      activityToken = activity.begin({ id: `story-${kind}`, label: operationNames[kind], owner: "story", cancel: cancelRequest });
      if (!activityToken) { busy = null; pendingRequest = null; activeController = null; rejectAttempt(kind, activity.conflict(`story-${kind}`, operationNames[kind])); return; }
    }
    refresh();
    if (kind in draftOperations) { status.textContent = "Preparing Story Planner proposal…"; actionStatus.textContent = ""; }
    else actionStatus.textContent = kind === "apply" ? "Applying draft…" : "Preparing revision…";
    if (kind === "apply") onApplyBusy(true);
    try {
      const result = await operation(projectId, request, controller?.signal);
      if (token !== epoch || projectId !== getContext().projectId) return;
      if (kind === "apply") {
        const wasNextDraft = draftOperation === "next", wasInsertDraft = draftOperation === "insert", wasReplanDraft = draftOperation === "replan";
        adoptProject(result); clearDraft({ clearNextInstruction: wasNextDraft, clearInsertInstruction: wasInsertDraft, clearReplanInstruction: wasReplanDraft }); status.textContent = "Draft applied. The Sequence is saved.";
      } else {
        if (kind in draftOperations) {
          const source = kind === "initial-range" ? emptyStoryMemory() : kind === "replan" ? result.authority.memoryBeforeRange : memory;
          projectMemoryStates(source, result[draftOperations[kind].items]);
          selected.clear();
        }
        referencesStale = false;
        draftOperation = kind === "revise" ? "revision" : kind; draft = structuredClone(result); renderDraft();
        if (kind in draftOperations) status.textContent = "Editable draft only. Review and Apply when ready.";
        else actionStatus.textContent = "Revision applied to the draft. Review the result before Apply.";
      }
    } catch (error) {
      if (token === epoch) {
        if ((kind in draftOperations || kind === "revise") && typeof error.rawResponse === "string") {
          failedRawResponse = error.rawResponse; rawResponseAction.hidden = false;
        }
        const message = error.rawResponseWithheld
          ? `${error.message} Raw response was withheld because it echoed request data.`
          : error.message;
        if (kind in draftOperations) status.textContent = message;
        else actionStatus.textContent = message;
      }
    } finally {
      if (kind === "apply") onApplyBusy(false);
      if (token === epoch) { stopRunning(); busy = null; pendingRequest = null; refresh(); }
    }
  }

  function canAttempt(kind) {
    if (referencesStale && (kind === "apply" || kind === "revise"))
      return rejectAttempt(kind, "Reference Assets changed. Discard this stale draft and build a new Story proposal.");
    const providerKind = kind === "build" ? "initial-range" : kind;
    if (busy) {
      const message = kind === "apply" ? "A Story Planner action is already in progress." : activity.conflict(`story-${providerKind}`, operationNames[providerKind]);
      (kind === "apply" || kind === "revise" ? actionStatus : status).textContent = message;
      return false;
    }
    if (kind !== "apply" && activity.active()) return rejectAttempt(kind, activity.conflict(`story-${providerKind}`, operationNames[providerKind]));
    return true;
  }
  function rejectAttempt(kind, message) {
    (kind === "apply" || kind === "revise" ? actionStatus : status).textContent = message;
    return false;
  }
  function validPlannerRequest(kind) {
    const context = getContext();
    if (!storyEligible(context)) return rejectAttempt(kind, context.dirty ? "Save the current Project before using Story Planner." : "Save a filesystem Project before using Story Planner.");
    const seconds = Number(timeout.value);
    if (!Number.isFinite(seconds) || seconds < 1 || seconds > 3600) return rejectAttempt(kind, "Enter a Story Planner timeout from 1 to 3600 seconds.");
    return true;
  }
  function requestedShotGuidance(kind, control) {
    if (control.mode.value === "auto") return {};
    const value = control.count.value;
    const count = Number(value);
    if (!/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(count)) {
      rejectAttempt(kind, "Enter a positive whole number of shots per Generation.");
      return null;
    }
    return { shotGuidance: { mode: control.mode.value, count } };
  }

  async function buildDraft() {
    refresh(); if (!canAttempt("build") || !validPlannerRequest("build")) return;
    if (!storyInitialRangeEligible(getContext(), Number(count.value))) return rejectAttempt("build", "Build Initial Range requires one saved Generation and a total count from 2 to 12.");
    if (!idea.value.trim()) return rejectAttempt("build", "Enter and save a Story idea before Build Initial Range.");
    const guidance = requestedShotGuidance("build", buildShots); if (!guidance) return;
    if (draft && !confirmDiscard("Replace the existing Story Planner draft?")) return;
    if (typeof api.buildInitialRange !== "function") throw new Error("Missing Build Initial Range API");
    await run(api.buildInitialRange,
      { requestedTotalCount: Number(count.value), llm: plannerSettings(), ...guidance }, "initial-range");
  }
  async function planNextDraft() {
    refresh(); if (!canAttempt("next") || !validPlannerRequest("next")) return;
    if (!storyNextEligible(getContext(), Number(nextCount.value))) return rejectAttempt("next", "Plan Next requires the terminal Generation, trusted Story Memory, and a count from 1 to 4.");
    const guidance = requestedShotGuidance("next", nextShots); if (!guidance) return;
    if (draft && !confirmDiscard("Replace the existing Story Planner draft?")) return;
    await run(api.planStoryNext, { anchorGenerationId: getContext().activeGenerationId,
      requestedCount: Number(nextCount.value), instruction: nextInstruction.value, llm: plannerSettings(), ...guidance }, "next");
  }
  async function planInsertDraft() {
    refresh(); if (!canAttempt("insert") || !validPlannerRequest("insert")) return;
    if (!storyInsertEligible(getContext(), Number(insertCount.value))) return rejectAttempt("insert", "Plan Insert requires a nonterminal Generation, trusted Story Memory, and a count from 1 to 4.");
    const guidance = requestedShotGuidance("insert", insertShots); if (!guidance) return;
    if (draft && !confirmDiscard("Replace the existing Story Planner draft?")) return;
    await run(api.planStoryInsert, { upstreamGenerationId: getContext().activeGenerationId,
      requestedCount: Number(insertCount.value), instruction: insertInstruction.value, llm: plannerSettings(), ...guidance }, "insert");
  }
  async function planReplanDraft() {
    refresh(); if (!canAttempt("replan") || !validPlannerRequest("replan")) return;
    if (!storyReplanEligible(getContext(), replanStart.value, replanEnd.value)) return rejectAttempt("replan", "Choose a valid range of up to 6 Generations with trusted upstream Story Memory.");
    const guidance = requestedShotGuidance("replan", replanShots); if (!guidance) return;
    if (draft && !confirmDiscard("Replace the existing Story Planner draft?")) return;
    await run(api.planStoryReplan, { startGenerationId: replanStart.value, endGenerationId: replanEnd.value,
      instruction: replanInstruction.value, llm: plannerSettings(), ...guidance }, "replan");
  }
  async function reviseDraft() {
    refresh(); if (!canAttempt("revise") || !validPlannerRequest("revise")) return;
    if (!draft) return rejectAttempt("revise", "There is no draft to revise.");
    if (isBoundedDraft()) return rejectAttempt("revise", "Bounded Story drafts cannot use Revise Selected.");
    if (!selected.size || !scopes.size || !instruction.value.trim()) return rejectAttempt("revise", "Select Generations and fields, then enter a revision instruction.");
    await run(api.reviseStoryPlan, { draft: structuredClone(draft), selectedPlanItemIds: [...selected],
      scopes: [...scopes], instruction: instruction.value, llm: plannerSettings() }, "revise");
  }
  async function applyDraft() {
    refresh(); if (!canAttempt("apply")) return;
    if (!draft) return rejectAttempt("apply", "There is no draft to apply.");
    const context = getContext();
    if (!context.saved || !context.projectId) return rejectAttempt("apply", "Save a filesystem Project before applying this Story draft.");
    if (context.dirty) return rejectAttempt("apply", "Save or discard the current Project edits before applying this Story draft.");
    await run(api[draftOperations[draftOperation]?.apply || "applyStoryPlan"], { draft: structuredClone(draft) }, "apply");
  }
  contextInput.addEventListener("input", () => { if (draft && !busy) draft.sequenceContext = contextInput.value; });
  idea.addEventListener("input", () => onIdeaChange(idea.value));
  for (const input of [idea, count, nextCount, nextInstruction, insertCount, insertInstruction, replanInstruction, instruction]) input.addEventListener("input", refresh);
  timeout.addEventListener("input", () => { savePreferences(); refresh(); });
  replanStart.addEventListener("change", refresh); replanEnd.addEventListener("change", refresh);
  next.addEventListener("click", planNextDraft);
  insert.addEventListener("click", planInsertDraft);
  replan.addEventListener("click", planReplanDraft);
  build.addEventListener("click", buildDraft); revise.addEventListener("click", reviseDraft);
  apply.addEventListener("click", applyDraft); discard.addEventListener("click", () => {
    if (busy !== "apply") clearDraft({ clearNextInstruction: draftOperation === "next", clearInsertInstruction: draftOperation === "insert", clearReplanInstruction: draftOperation === "replan" });
  });
  refresh();
  return {
    refresh, confirmContextChange, clearDraft, resetProjectContext, restoreRevisionDraft,
    invalidateReferences() {
      activeController?.abort(); stopRunning(); epoch++; busy = null; pendingRequest = null;
      referencesStale = Boolean(draft);
      if (referencesStale) actionStatus.textContent = "Reference Assets changed. This Story draft is stale; discard it and build a new proposal.";
      refresh();
    },
    get storyIdea() { return idea.value; },
    restoreStoryIdea(value) { idea.value = value ?? ""; refresh(); },
    restoreScenePlan(value) { appliedScenePlan.value = value ?? ""; },
    get storyCreativeDirection() { return creativeDirection.payload(); },
    get storyPlanningStyle() { return { ...planningStyleValues }; },
    restoreStoryPlanningStyle(value) { restorePlanningStyle(value); refresh(); },
    restoreStoryCreativeDirection(value) {
      creativeDirection.restore(value);
      refresh();
    },
    buildDraft, reviseDraft, applyDraft, planNextDraft, planInsertDraft, planReplanDraft,
    get memoryEdited() { return memoryEdited; },
    get storyMemory() { return structuredClone(memory); },
    get storyMemoryForSave() { return memoryPayload(); },
    restoreStoryMemory(value) { memoryEdited = false; memory = structuredClone(value || emptyStoryMemory()); transientMemoryIds.clear(); renderMemory(); refresh(); },
    restorePreferences(value) {
      timeout.value = String(value?.timeoutSeconds ?? 600);
      reasoning.checked = value?.reasoning !== false;
      (reasoning.parentElement || reasoning.parent).querySelector(".story-planner-toggle-state").textContent = reasoning.checked ? "On" : "Off";
      vision.checked = value?.vision === true;
      (vision.parentElement || vision.parent).querySelector(".story-planner-toggle-state").textContent = vision.checked ? "On" : "Off";
      refresh();
    },
    get hasDraft() { return Boolean(draft || busy); },
    get hasPendingRequest() { return Boolean(busy && busy !== "apply"); },
    get activeOperation() { return busy && busy !== "apply" ? operationNames[busy] : null; },
    pendingAffectsObservedGeneration(generationId) {
      if (!pendingRequest) return false;
      const { kind, request } = pendingRequest;
      if (kind === "revise") return true;
      const generations = getContext().generations;
      const index = generations.findIndex(generation => generation.generationId === generationId);
      if (index < 0) return false;
      if (kind === "next") return index >= Math.max(0, generations.length - 3);
      if (kind === "insert") {
        const anchor = generations.findIndex(generation => generation.generationId === request.upstreamGenerationId);
        return anchor >= 0 && index >= Math.max(0, anchor - 2) && index <= anchor + 2;
      }
      if (kind === "replan") {
        const start = generations.findIndex(generation => generation.generationId === request.startGenerationId);
        const end = generations.findIndex(generation => generation.generationId === request.endGenerationId);
        return start >= 0 && end >= start && index >= Math.max(0, start - 3) && index <= end + 2;
      }
      return false;
    },
  };
}
