import { runInNewContext } from "node:vm";
import assert from "node:assert/strict";
import test from "node:test";
import { createStoryPlanner, storyEligible, storyNextEligible, storyInsertEligible, storyReplanEligible, storyPlannerSettings, projectMemoryStates, emptyStoryMemory } from "../../web/js/story-planning.js";
import { shouldMarkWorkspaceDirty } from "../../web/js/sequence-workflow.js";
import { api as realApi } from "../../web/js/api.js";
import { persistFilesystemProject } from "../../web/js/project-workflow.js";
import { readFile } from "node:fs/promises";
import { DIRECTION_GROUPS, STORY_DIRECTION_GUIDANCE, encodeDirectionField, decodeDirectionField } from "../../web/js/story-creative-direction.js";
import { mountHtml, domOrder } from "./support/html.mjs";
import { storyGenerationFeedback } from "../../web/js/story-generation-feedback.js";
import { createProviderActivity, mountProviderActivity } from "../../web/js/provider-activity.js";

test("Reference Asset edits stale a Story proposal without rewriting its prose", async () => {
  const s = nextSetup();
  await s.planner.planNextDraft();
  const prose = s.cards.textContent;
  const before = s.calls.length;
  s.planner.invalidateReferences();
  assert.equal(s.cards.textContent, prose);
  await s.planner.applyDraft();
  assert.equal(s.calls.length, before);
  assert.match(s.find("action-status").textContent, /Reference Assets changed/);
});

for (const [operation, make, run] of [
  ["build", () => setup(), s => s.planner.buildDraft()],
  ["next", () => nextSetup(), s => s.planner.planNextDraft()],
  ["insert", () => insertSetup(), s => s.planner.planInsertDraft()],
  ["replan", () => replanSetup(), s => s.planner.planReplanDraft()],
]) {
  test(`${operation} uses operation-local per-Generation shot guidance`, async () => {
    const s = make();
    if (operation === "build") s.planner.restoreStoryIdea("A saved story premise");
    const mode = s.find(`${operation}-shot-guidance`);
    const count = s.find(`${operation}-shots-per-generation`);
    assert.equal(mode.value, "auto");
    assert.equal(count.parent.hidden, true);
    assert.match(mode.parent.parent.textContent, /Applies independently to each Generation/);
    await run(s);
    assert.equal(s.calls.at(-1).body.shotGuidance, undefined);
    for (const choice of ["prefer", "exact"]) {
      mode.value = choice; await mode.emit("change");
      assert.equal(count.parent.hidden, false);
      assert.equal(count.disabled, false);
      count.value = ""; const before = s.calls.length;
      await run(s);
      assert.equal(s.calls.length, before);
      assert.match(s.find("status").textContent, /positive whole number of shots per Generation/);
      for (const invalid of ["0", "-1", "1.5", "4e0"]) {
        count.value = invalid; await run(s); assert.equal(s.calls.length, before);
      }
      count.value = "4";
      await run(s);
      assert.deepEqual(s.calls.at(-1).body.shotGuidance, { mode: choice, count: 4 });
    }
    mode.value = "auto"; await mode.emit("change");
    assert.equal(count.parent.hidden, true);
    await run(s);
    assert.equal(s.calls.at(-1).body.shotGuidance, undefined);
    mode.value = "exact"; await mode.emit("change");
    s.planner.resetProjectContext();
    assert.equal(mode.value, "auto");
    assert.equal(count.value, "");
    assert.equal(count.parent.hidden, true);
  });
}

test("Creative Direction canonical codec exhaustively round-trips control combinations without Auto noise", () => {
  for (const [field, , controls] of DIRECTION_GROUPS) {
    const defaults = Object.fromEntries(controls.map(([key]) => [key, "auto"]));
    assert.equal(encodeDirectionField(field, defaults), "");
    assert.deepEqual(decodeDirectionField(field, ""), defaults);
    function check(index, values) {
      if (index === controls.length) {
        const text = encodeDirectionField(field, values);
        assert.ok(!text.toLowerCase().includes("auto"));
        assert.deepEqual(decodeDirectionField(field, text), values);
        return;
      }
      const [key, , levels] = controls[index];
      for (const value of levels) {
        const actual = encodeDirectionField(field, { ...defaults, [key]: value });
        assert.equal(actual, value === "auto" ? "" : STORY_DIRECTION_GUIDANCE[key][value]);
        check(index + 1, { ...values, [key]: value });
      }
    }
    check(0, defaults);
    for (const text of ["  ", "slow pacing.", "Slow pacing", "Slow pacing.\n", "Slow-burn opening, then rapidly escalate.", "Moderate horror; moderate horror."]) {
      assert.equal(decodeDirectionField(field, text), null);
    }
  }
});

test("mounted direction defaults, encoding, reset, exact reload and protected Custom replacement", async () => {
  const s = setup({ canonicalIdea: true });
  const blank = Object.fromEntries(DIRECTION_GROUPS.map(([field]) => [field, ""]));
  assert.deepEqual(s.planner.storyCreativeDirection, blank);
  for (const [field, , controls] of DIRECTION_GROUPS) {
    for (const [key, , levels] of controls) {
      assert.equal(s.find(`direction-${key}`).value, String(levels.indexOf("auto")));
      await s.type(s.find(`direction-${key}`), "4");
    }
    assert.notEqual(s.planner.storyCreativeDirection[field], "");
  }
  assert.equal(s.context.dirty, true);
  const encoded = s.planner.storyCreativeDirection;
  const reopened = setup(); reopened.planner.restoreStoryCreativeDirection(encoded);
  assert.deepEqual(reopened.planner.storyCreativeDirection, encoded);
  for (const [, , controls] of DIRECTION_GROUPS) for (const [key] of controls) assert.equal(reopened.find(`direction-${key}`).value, "4");
  await s.find("reset-creative-direction").emit("click");
  assert.deepEqual(s.planner.storyCreativeDirection, blank);
  assert.equal(s.prompts.length, 0);
  for (const [, , controls] of DIRECTION_GROUPS) for (const [key, , levels] of controls) {
    assert.equal(s.find(`direction-${key}`).value, String(levels.indexOf("auto")));
  }
  const exact = "  Slow pacing.\r\n🙂 Slow-burn opening, then escalate.  ";
  s.planner.restoreStoryCreativeDirection({ pacingRhythm: exact });
  assert.equal(s.find("creative-pacing-rhythm").closest("details").open, true);
  assert.equal(s.find("direction-pacing"), null);
  s.approve(false);
  await s.find("structured-pacingRhythm").emit("click");
  await s.find("reset-creative-direction").emit("click");
  assert.equal(s.prompts.length, 2);
  assert.equal(s.planner.storyCreativeDirection.pacingRhythm, exact);
  const saved = await persistFilesystemProject({ projectId: "project", workspace: {}, storyCreativeDirection: s.planner.storyCreativeDirection,
    saveProject: async (id, workspace, continuity, planning, idea, memory, authority, direction) => ({ storyCreativeDirection: structuredClone(direction) }) });
  reopened.planner.restoreStoryCreativeDirection(saved.response.storyCreativeDirection);
  assert.equal(reopened.planner.storyCreativeDirection.pacingRhythm, exact);
  s.approve(true); await s.find("structured-pacingRhythm").emit("click");
  assert.equal(s.find("direction-pacing").value, "2");
  assert.deepEqual(s.planner.storyCreativeDirection, blank);
  await s.type(s.find("creative-tone-mood"), exact);
  await s.find("reset-creative-direction").emit("click");
  assert.deepEqual(s.planner.storyCreativeDirection, blank);
});

test("missing bounded memory patches fail explicitly and preserve a previous valid draft", async () => {
  assert.throws(() => projectMemoryStates(emptyStoryMemory(), [{}]), /memoryPatchAfter/);
  const s = setup(); await s.build();
  s.api.buildInitialRange = async () => proposal();
  await s.planner.buildDraft();
  assert.match(s.find("status").textContent, /memoryPatchAfter/);
  await s.planner.applyDraft();
  assert.ok(s.calls.at(-1).body.draft.items.every(item => item.memoryPatchAfter));
});

test("missing bounded API never silently invokes legacy Build", async () => {
  const s = setup(); delete s.api.buildInitialRange;
  s.api.buildStoryPlan = () => assert.fail("legacy Build must not run");
  await s.type(s.find("idea"), "A start");
  await assert.rejects(s.planner.buildDraft(), /Missing Build Initial Range API/);
});

test("Creative Direction semantic presets retain legacy compatibility and keep Custom prose exact", () => {
  assert.deepEqual(decodeDirectionField("toneMood", "Strong sensuality; subtle tension."), { sensuality: "strong", drama: "auto", horror: "auto", tension: "subtle", romance: "auto", whimsy: "auto" });
  assert.deepEqual(decodeDirectionField("performanceEnergy", "Low energy."), { performanceStyle: "auto", performanceEnergy: "low" });
  assert.deepEqual(decodeDirectionField("pacingRhythm", "Brisk pacing."), { pacing: "brisk" });
  const custom = "  My exact\r\ncustom direction.  ";
  assert.equal(decodeDirectionField("pacingRhythm", custom), null);
  assert.match(encodeDirectionField("performanceEnergy", { performanceStyle: "expressive", performanceEnergy: "low" }), /body language/);
  assert.match(encodeDirectionField("performanceEnergy", { performanceStyle: "expressive", performanceEnergy: "low" }), /low-key/);
  assert.notEqual(encodeDirectionField("pacingRhythm", { pacing: "brisk" }), encodeDirectionField("beatDurationDensity", { density: "sparse" }));
});

for (const operation of ["insert", "replan", "revision"]) {
  test(`${operation} discards pending responses across a Project switch`, async () => {
    const s = operation === "insert" ? insertSetup() : operation === "replan" ? replanSetup() : setup();
    let finish;
    const wait = () => new Promise(resolve => { finish = resolve; });
    let pending, response;
    if (operation === "insert") {
      s.api.planStoryInsert = wait; response = insertProposal(); pending = s.planner.planInsertDraft();
    } else if (operation === "replan") {
      s.api.planStoryReplan = wait; response = replanProposal(); pending = s.planner.planReplanDraft();
    } else {
      s.ordinary(); await s.select(1); await s.check(s.find("scope-camera_intent"));
      await s.type(s.find("instruction"), "Widen the shot");
      s.api.reviseStoryPlan = wait; response = proposal(); pending = s.planner.reviseDraft();
    }
    await s.find("discard").emit("click");
    s.context.projectId = "other-project";
    finish(response); await pending;
    assert.equal(s.planner.hasDraft, false);
    assert.equal(s.cards().length, 0);
    assert.equal(s.adopted.length, 0);
  });
}

test("Replan starting at G1 keeps its no-predecessor relationship locked after refresh", async () => {
  const s = replanSetup();
  s.api.planStoryReplan = async () => replanProposal(1, 2);
  s.find("replan-start").value = "g-1"; await s.find("replan-start").emit("change");
  s.find("replan-end").value = "g-2"; await s.find("replan-end").emit("change");
  await s.planner.planReplanDraft(); s.planner.refresh();
  assert.equal(s.input(0, "relationshipToPrevious").disabled, true);
  assert.equal(s.input(1, "relationshipToPrevious").disabled, false);
});

// Small DOM test double: exercise mounted controls/events, never inspect JS source.
class Element {
  constructor(tag, doc) {
    this.tagName = tag; this.ownerDocument = doc; this.children = []; this.dataset = {};
    this.events = {}; this.attributes = {}; this.value = ""; this.checked = false;
  }
  get parentNode() { return this.parent; }
  set value(value) { this.rawValue = this.tagName === "textarea" ? String(value).replace(/\r\n?/g, "\n") : value; }
  get value() { return this.rawValue; }
  append(...children) { for (const child of children) { if (child.parent) child.parent.children = child.parent.children.filter(node => node !== child); child.parent = this; this.children.push(child); } }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || "") + this.children.map(child => child.textContent).join(""); }
  set innerHTML(_) { throw new Error("Unsafe HTML rendering"); }
  setAttribute(key, value) { this.attributes[key] = value; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  addEventListener(name, handler) { (this.events[name] ||= []).push(handler); }
  async emit(name) { for (const handler of this.events[name] || []) await handler({ target: this }); }
  matches(selector) {
    if (selector.startsWith("#")) return this.id === selector.slice(1);
    if (selector.startsWith(".")) return (this.className || "").split(" ").includes(selector.slice(1));
    return this.tagName === selector;
  }
  querySelectorAll(selectors) {
    const matches = [];
    for (const child of this.children) {
      if (selectors.split(",").some(selector => child.matches(selector.trim()))) matches.push(child);
      matches.push(...child.querySelectorAll(selectors));
    }
    return matches;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return this.matches(selector) ? this : this.parent?.closest(selector); }
}
const doc = { createElement: tag => new Element(tag, doc) };

function nextProposal() {
  return { authority: { anchorOrdinal: 60, slotIds: ["slot-1", "slot-2", "slot-3"] },
    authorityToken: "opaque authority", slots: proposal().items.slice(1).map((item, i) => {
      const { existingGenerationId, ...fields } = item;
      return { ...fields, planItemId: `slot-${i + 1}`, memoryPatchAfter: i === 2 ? {
        recentSummary: "The key was returned.", factUpserts: [{ id: "fact-slot", text: "Carol now holds the key." }],
        resolvedThreadIds: ["promise"] } : {} };
    }), warnings: ["Check the station closing time."] };
}

function nextSetup(options = {}) {
  const s = setup(options);
  s.context.generations = Array.from({ length: 60 }, (_, i) => ({ generationId: `g-${i + 1}`, storyMemoryStatus: "trusted" }));
  s.context.activeGenerationId = "g-60";
  s.api.planStoryNext = async (id, body) => { s.calls.push({ action: "next", id, body }); return nextProposal(); };
  s.api.applyStoryNext = async (id, body) => { s.calls.push({ action: "next-apply", id, body }); return { id, storyMemory: {} }; };
  s.planner.refresh();
  return s;
}

function insertProposal() {
  return { authority: { upstreamOrdinal: 3, downstreamOrdinal: 4, slotIds: ["insert-1", "insert-2"] },
    authorityToken: "opaque authority", slots: ["insert-1", "insert-2"].map((planItemId, index) => ({
      planItemId, scenePlan: `Bridge ${index + 1}`, dialogue: "", incomingState: "Bridge intact",
      intendedOutgoingState: "Reach protected boundary", relationshipToPrevious: "new_shot_preserve_continuity",
      cameraIntent: "Medium", referenceRecommendations: [], memoryPatchAfter: index === 0 ? {
        recentSummary: "Alice steps onto the intact bridge.", factUpserts: [], resolvedFactIds: [], threadUpserts: [], resolvedThreadIds: [],
      } : {},
    })), warnings: [] };
}

function insertSetup(options = {}) {
  const s = setup(options);
  s.context.generations = Array.from({ length: 5 }, (_, i) => ({ generationId: `g-${i + 1}`, storyMemoryStatus: "trusted" }));
  s.context.activeGenerationId = "g-3";
  s.api.planStoryInsert = async (id, body) => { s.calls.push({ action: "insert", id, body }); return insertProposal(); };
  s.api.applyStoryInsert = async (id, body) => { s.calls.push({ action: "insert-apply", id, body }); return { id, sequence: { generations: [] } }; };
  s.planner.refresh();
  return s;
}

function replanProposal(start = 3, count = 3) {
  return { authority: { startOrdinal: start, endOrdinal: start + count - 1,
    memoryBeforeRange: { recentSummary: "Bridge intact; delivery unresolved.", durableFacts: [], openThreads: [] } },
  authorityToken: "opaque authority", items: Array.from({ length: count }, (_, index) => ({
    existingGenerationId: `g-${start + index}`, scenePlan: `Replanned beat ${index + 1}`, dialogue: "",
    incomingState: "Bridge intact", intendedOutgoingState: "Keep the delivery unresolved",
    relationshipToPrevious: "new_shot_preserve_continuity", cameraIntent: "Tense close shot",
    referenceRecommendations: [], memoryPatchAfter: index === 0 ? { recentSummary: "Alice approaches the intact bridge." } : {},
  })), warnings: [] };
}

function replanSetup(options = {}) {
  const s = setup(options);
  s.context.generations = Array.from({ length: 8 }, (_, i) => ({ generationId: `g-${i + 1}`, storyMemoryStatus: "trusted" }));
  s.context.activeGenerationId = "g-3";
  s.api.planStoryReplan = async (id, body) => { s.calls.push({ action: "replan", id, body }); return replanProposal(); };
  s.api.applyStoryReplan = async (id, body) => { s.calls.push({ action: "replan-apply", id, body }); return { id, sequence: { generations: [] } }; };
  s.planner.refresh();
  return s;
}

for (const outcome of ["success", "provider_error", "conflict"]) {
  test(`pending Replan guards canonical edits and recovers after ${outcome}`, async () => {
    const s = replanSetup();
    let finish, fail;
    s.api.planStoryReplan = () => new Promise((resolve, reject) => { finish = resolve; fail = reject; });
    await s.type(s.find("replan-instruction"), "Keep this for retry");
    const pending = s.planner.planReplanDraft();
    assert.equal(s.planner.hasPendingRequest, true);
    assert.equal(s.planner.pendingAffectsObservedGeneration("g-3"), true);
    assert.equal(s.planner.pendingAffectsObservedGeneration("g-8"), false);
    assert.equal(Boolean(s.find("replan").disabled), false);
    assert.equal(s.planner.confirmContextChange({ message: "Save the Project?" }), false);
    assert.match(s.find("status").textContent, /Wait for the Story proposal request/);
    assert.equal(s.planner.confirmContextChange({ allowPendingSelection: true }), true);
    assert.equal(s.planner.hasPendingRequest, true);
    if (outcome === "success") finish(replanProposal());
    else fail(new Error(outcome === "conflict" ? "Relevant Replan Range Story authority changed; plan again" : "Provider unavailable"));
    await pending;
    assert.equal(s.planner.hasPendingRequest, false);
    assert.equal(s.planner.pendingAffectsObservedGeneration("g-3"), false);
    assert.equal(Boolean(s.find("replan").disabled), false);
    if (outcome === "success") assert.equal(s.planner.hasDraft, true);
    else {
      assert.equal(s.planner.hasDraft, false);
      assert.equal(s.find("replan-instruction").value, "Keep this for retry");
      assert.match(s.find("status").textContent, outcome === "conflict" ? /authority changed/ : /Provider unavailable/);
    }
  });
}

test("Plan Next is eligible at 60, legacy Build stays bounded, and count/anchor are guarded", async () => {
  const s = nextSetup();
  assert.equal(storyEligible(s.context), true);
  assert.equal(Boolean(s.find("next").disabled), false);
  await s.type(s.find("idea"), "Idea");
  assert.equal(Boolean(s.find("build").disabled), false);
  for (const count of [0, 5, 1.5, NaN]) assert.equal(storyNextEligible(s.context, count), false);
  for (const count of [1, 2, 3, 4]) assert.equal(storyNextEligible(s.context, count), true);
  s.context.activeGenerationId = "g-59"; s.planner.refresh();
  await s.planner.planNextDraft();
  assert.equal(s.calls.length, 0);
  assert.match(s.find("next-anchor").textContent, /Select the last Generation/);
});

test("selected G3/G6 memory survives normal Save, timeline switches and browser reload independently", async () => {
  const early = "Alice has reached the open railway underpass while travelling to deliver medicine to Bob before curfew.";
  const late = "Alice crossed the railway underpass earlier. The underpass was later sealed after an electrical fire, and Alice has since delivered the medicine package to Bob.";
  let disk = Array.from({ length: 6 }, (_, i) => ({ generationId: `g-${i + 1}`, storyMemoryStatus: "trusted",
    storyMemoryAfter: { recentSummary: "", durableFacts: [], openThreads: [] } }));
  const mount = () => {
    const s = setup({ onSelectGeneration: async id => {
      s.context.activeGenerationId = id;
      s.planner.restoreStoryMemory(disk.find(g => g.generationId === id).storyMemoryAfter);
    }, onConfirmMemory: async id => save(id) });
    s.context.generations = disk.map(({ storyMemoryAfter, ...g }) => ({ ...g }));
    const save = async id => {
      const saved = await persistFilesystemProject({ projectId: "project", workspace: {},
        storyMemory: s.planner.storyMemoryForSave, memoryAuthority: { storyMemoryGenerationId: id, expectedProjectFingerprint: "loaded" },
        saveProject: async (pid, workspace, continuity, planning, idea, memory, authority) => {
          assert.equal(authority.storyMemoryGenerationId, s.context.activeGenerationId);
          assert.equal(authority.expectedProjectFingerprint, "loaded");
          disk.find(g => g.generationId === authority.storyMemoryGenerationId).storyMemoryAfter = structuredClone(memory);
          return { id: pid, storyMemoryAfter: structuredClone(memory) };
        } });
      s.context.dirty = false; s.planner.restoreStoryMemory(saved.response.storyMemoryAfter);
    };
    s.planner.refresh();
    return { ...s, save, choose: id => s.find(`memory-timeline-${id}`).emit("click") };
  };
  let s = mount();
  await s.choose("g-3");
  assert.equal(s.find("memory-title").textContent, "Story Memory After Generation 3");
  assert.equal(s.find("memory-summary").disabled, false);
  await s.type(s.find("memory-summary"), early);
  await s.find("add-durableFacts").emit("click");
  const fact = s.planner.storyMemory.durableFacts[0].id;
  await s.type(s.find(`memory-${fact}`), "The railway underpass is open.");
  await s.find("add-openThreads").emit("click");
  const thread = s.planner.storyMemory.openThreads[0].id;
  await s.type(s.find(`memory-${thread}`), "Alice still needs to deliver the medicine to Bob.");
  await s.save("g-3");
  await s.choose("g-6");
  await s.type(s.find("memory-summary"), late);
  await s.find("add-durableFacts").emit("click");
  const laterFact = s.planner.storyMemory.durableFacts[0].id;
  await s.type(s.find(`memory-${laterFact}`), "The railway underpass is sealed after an electrical fire.");
  await s.save("g-6");
  for (let reload = 0; reload < 2; reload++) {
    for (const [id, expected, factText, threadCount] of [["g-3", early, "The railway underpass is open.", 1],
      ["g-6", late, "The railway underpass is sealed after an electrical fire.", 0]]) {
      await s.choose(id);
      assert.equal(s.find("memory-summary").value, expected);
      assert.equal(s.planner.storyMemory.durableFacts[0].text, factText);
      assert.equal(s.planner.storyMemory.openThreads.length, threadCount);
    }
    disk = JSON.parse(JSON.stringify(disk)); s = mount();
  }
  await s.choose("g-6");
  await s.find(`remove-${laterFact}`).emit("click"); await s.save("g-6");
  await s.choose("g-3");
  assert.equal(s.planner.storyMemory.durableFacts[0].text, "The railway underpass is open.");
});

test("memory trust labels, confirmation target and terminal eligibility", async () => {
  let confirmed;
  const s = setup({ onConfirmMemory: id => { confirmed = id; } });
  s.context.generations = [{ generationId: "g-3", storyMemoryStatus: "legacy_unknown" },
    { generationId: "g-6", storyMemoryStatus: "needs_review" }];
  s.context.activeGenerationId = "g-3"; s.planner.refresh();
  assert.match(s.find("memory-trust").textContent, /Legacy history unknown/);
  assert.match(s.find("memory-trust").textContent, /do not mean the story was empty/);
  assert.equal(s.find("memory-summary").disabled, false);
  assert.match(s.find("memory-timeline").textContent, /Needs review/);
  s.context.activeGenerationId = "g-6"; s.planner.refresh();
  assert.equal(storyNextEligible(s.context, 3), false);
  assert.match(s.find("next-anchor").textContent, /Review and confirm/);
  await s.find("confirm-memory").emit("click");
  assert.equal(confirmed, "g-6"); assert.equal(s.planner.memoryEdited, true);
  s.context.dirty = false; s.context.generations[1].storyMemoryStatus = "trusted";
  s.planner.restoreStoryMemory({ recentSummary: "", durableFacts: [], openThreads: [] });
  assert.equal(storyNextEligible(s.context, 3), true);
  assert.match(s.find("memory-trust").textContent, /^Trusted/);
  assert.match(s.find("memory-timeline").textContent, /Legacy history unknown/);
});

test("fresh Story Planner and Continuity Authoring Notes disclosures are collapsed", async () => {
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  for (const id of ["story-planner-section", "continuity-section"]) {
    const opening = html.match(new RegExp(`<details[^>]*id="${id}"[^>]*>`))[0];
    assert.doesNotMatch(opening, /\bopen\b/);
  }
});

test("Plan Next renders only new slots, visible memory delta, warnings, and editable fields", async () => {
  const s = nextSetup();
  s.planner.restoreStoryMemory({ recentSummary: "Before", durableFacts: [], openThreads: [{ id: "promise", text: "Return the key", relatedSubjectIds: [] }] });
  await s.type(s.find("next-instruction"), "Resolve the promise");
  await s.planner.planNextDraft();
  assert.deepEqual(s.calls[0].body, { anchorGenerationId: "g-60", requestedCount: 3,
    instruction: "Resolve the promise", llm: { modelId: "fake", timeoutSeconds: 600, reasoningMode: "on", storyPlannerVision: false } });
  assert.equal(s.cards().length, 3);
  assert.match(s.cards()[0].textContent, /Generation 61/);
  assert.equal(s.input(0, "relationshipToPrevious").disabled, false);
  assert.match(s.cards()[2].textContent, /Carol now holds the key/);
  assert.match(s.cards()[0].textContent, /Return the key/);
  assert.doesNotMatch(s.cards()[0].textContent, /Carol now holds the key/);
  for (const card of s.cards()) assert.ok(!card.querySelector("details").open);
  assert.match(s.find("warnings").textContent, /station closing time/);
  assert.equal(Boolean(s.find("revise").disabled), false);
  await s.type(s.input(1, "dialogue"), "  Exact\nwords.  ");
  assert.equal(s.context.dirty, false);
  await s.planner.applyDraft();
  assert.equal(s.calls[1].action, "next-apply");
  assert.equal(s.calls[1].body.draft.slots[1].dialogue, "  Exact\nwords.  ");
  assert.equal(s.adopted.length, 1);
  assert.equal(s.planner.hasDraft, false);
  assert.equal(s.find("next-instruction").value, "");
});

test("canonical memory roundtrip, manual IDs, edits and removal follow Save conventions", async () => {
  const s = nextSetup();
  const initial = { recentSummary: "Old summary", durableFacts: [{ id: "fact", text: "Key held", relatedSubjectIds: ["alice"] }], openThreads: [] };
  s.planner.restoreStoryMemory(initial);
  await s.type(s.find("memory-summary"), "Exact\nsummary");
  await s.type(s.find("memory-fact"), "The key is damaged.");
  assert.equal(s.context.dirty, true);
  assert.equal(Boolean(s.find("next").disabled), false);
  assert.deepEqual(s.planner.storyMemory.durableFacts[0], { id: "fact", text: "The key is damaged.", relatedSubjectIds: ["alice"] });
  await s.find("add-openThreads").emit("click");
  const id = s.planner.storyMemory.openThreads[0].id;
  assert.match(id, /^[0-9a-f-]{36}$/);
  await s.type(s.find(`memory-${id}`), "Return the key.");
  let sent;
  const saved = await persistFilesystemProject({ projectId: "project", workspace: {}, storyMemory: s.planner.storyMemory,
    saveProject: async (id, workspace, continuity, planning, idea, storyMemory) => {
      sent = storyMemory; return { id, storyMemory };
    } });
  assert.deepEqual(sent, s.planner.storyMemory);
  s.context.dirty = false; s.planner.restoreStoryMemory(saved.response.storyMemory);
  await s.find(`remove-${id}`).emit("click");
  assert.equal(s.planner.storyMemory.openThreads.length, 0);
  assert.equal(s.context.dirty, true);
  s.planner.resetProjectContext();
  assert.equal(s.planner.storyMemory.recentSummary, "");
});

test("blank manual memory rows are omitted and clearing an existing entry removes it", async () => {
  const s = nextSetup();
  s.planner.restoreStoryMemory({ recentSummary: "Keep", durableFacts: [{ id: "fact", text: "Held", relatedSubjectIds: [] }], openThreads: [] });
  await s.find("add-durableFacts").emit("click");
  assert.equal(s.planner.storyMemory.durableFacts.length, 2);
  assert.deepEqual(s.planner.storyMemoryForSave.durableFacts, [{ id: "fact", text: "Held", relatedSubjectIds: [] }]);
  await s.type(s.find("memory-fact"), "");
  assert.equal(s.planner.storyMemory.durableFacts.length, 1);
  assert.equal(s.planner.storyMemory.durableFacts[0].text, "");
  let sent;
  await persistFilesystemProject({ projectId: "project", workspace: {}, storyMemory: s.planner.storyMemory,
    saveProject: async (id, workspace, continuity, planning, idea, storyMemory) => {
      sent = storyMemory; return { id, storyMemory };
    } });
  assert.deepEqual(sent.durableFacts, []);
});

test("Plan Next discard and Project/context switch reject late responses", async () => {
  for (const reset of ["clearDraft", "resetProjectContext"]) {
    const s = nextSetup();
    let resolve;
    s.api.planStoryNext = () => new Promise(done => { resolve = done; });
    const pending = s.planner.planNextDraft();
    s.planner[reset]();
    if (reset === "resetProjectContext") s.context.projectId = "different";
    resolve(nextProposal()); await pending;
    assert.equal(s.cards().length, 0);
    assert.equal(s.planner.hasDraft, false);
  }
});

test("failed Plan Next Apply retains the proposal, discard preserves canonical memory", async () => {
  const s = nextSetup();
  s.planner.restoreStoryMemory({ recentSummary: "Keep", durableFacts: [], openThreads: [] });
  await s.type(s.find("next-instruction"), "Resolve the old promise");
  await s.planner.planNextDraft();
  s.api.applyStoryNext = async () => { throw new Error("Memory changed; plan again"); };
  await s.planner.applyDraft();
  assert.equal(s.cards().length, 3);
  assert.match(s.find("action-status").textContent, /Memory changed/);
  await s.find("discard").emit("click");
  assert.equal(s.cards().length, 0);
  assert.equal(s.planner.storyMemory.recentSummary, "Keep");
  assert.equal(s.find("next-instruction").value, "");
});

test("failed Plan Next request preserves its instruction for retry", async () => {
  const s = nextSetup();
  await s.type(s.find("next-instruction"), "Keep this instruction");
  s.api.planStoryNext = async () => { throw new Error("Planner unavailable"); };
  await s.planner.planNextDraft();
  assert.equal(s.find("next-instruction").value, "Keep this instruction");
});

test("canonical Story idea dirties, saves, reloads and follows Project identity", async () => {
  const s = setup({ canonicalIdea: true });
  const exact = "  Rain and a dog.\n\n A reunion.  ";
  s.planner.restoreStoryIdea("Saved idea");
  assert.equal(s.context.dirty, false);
  await s.type(s.find("idea"), exact);
  assert.equal(s.context.dirty, true);
  await s.planner.buildDraft();
  assert.equal(s.calls.length, 0);
  let savedIdea;
  const saved = await persistFilesystemProject({ projectId: "project", workspace: {}, storyIdea: s.planner.storyIdea,
    saveProject: async (id, workspace, continuity, planning, storyIdea) => {
      savedIdea = storyIdea; return { id, workspace, storyIdea };
    },
  });
  s.context.dirty = false;
  s.planner.restoreStoryIdea(saved.response.storyIdea);
  await s.planner.buildDraft();
  assert.equal(s.calls[0].body.requestedTotalCount, 4);
  assert.equal(savedIdea, exact);
  s.planner.clearDraft();
  assert.equal(s.planner.storyIdea, exact);
  // Same-Project Generation response and a reopened editor restore canonical text.
  s.planner.restoreStoryIdea(savedIdea);
  const reopened = setup({ canonicalIdea: true });
  reopened.planner.restoreStoryIdea(savedIdea);
  assert.equal(reopened.planner.storyIdea, exact);
  s.planner.resetProjectContext(); s.context.projectId = "destination";
  s.planner.restoreStoryIdea("Destination idea");
  assert.equal(s.find("idea").value, "Destination idea");
  s.planner.resetProjectContext();
  assert.equal(s.planner.storyIdea, "");
});

test("Creative Direction is a compact, canonical Project editor that survives save and restore", async () => {
  const s = setup({ canonicalIdea: true });
  const saved = {
    toneMood: "Quietly ominous with dry humor.", performanceEnergy: "Restrained natural reactions.",
    pacingRhythm: "Slow burn, then urgency.", beatDurationDensity: "One meaningful development per Generation.",
  };
  s.planner.restoreStoryCreativeDirection(saved);
  assert.deepEqual(s.planner.storyCreativeDirection, saved);
  await s.type(s.find("creative-tone-mood"), "  Quiet unease\nwithout melodrama.  ");
  assert.equal(s.context.dirty, true);
  assert.equal(s.planner.storyCreativeDirection.toneMood, "  Quiet unease\nwithout melodrama.  ");
  let persisted;
  const result = await persistFilesystemProject({ projectId: "project", workspace: {},
    storyCreativeDirection: s.planner.storyCreativeDirection,
    saveProject: async (_id, _workspace, _continuity, _planning, _idea, _memory, _authority, direction) => {
      persisted = structuredClone(direction); return { id: "project", storyCreativeDirection: direction };
    },
  });
  assert.deepEqual(persisted, s.planner.storyCreativeDirection);
  const reopened = setup({ canonicalIdea: true });
  reopened.planner.restoreStoryCreativeDirection(result.response.storyCreativeDirection);
  assert.deepEqual(reopened.planner.storyCreativeDirection, persisted);
  reopened.planner.resetProjectContext();
  assert.deepEqual(reopened.planner.storyCreativeDirection, {
    toneMood: "", performanceEnergy: "", pacingRhythm: "", beatDurationDensity: "",
  });
});

test("Story idea survives failed operations, canceled navigation and successful Apply", async () => {
  const s = setup({ canonicalIdea: true });
  s.planner.restoreStoryIdea("Saved exact idea");
  s.api.buildInitialRange = async () => { throw new Error("Build failed"); };
  await s.planner.buildDraft();
  assert.equal(s.planner.storyIdea, "Saved exact idea");
  s.api.buildInitialRange = async () => initialRangeProposal();
  await s.planner.buildDraft();
  s.approve(false);
  assert.equal(s.planner.confirmContextChange({ dirty: true }), false);
  assert.equal(s.planner.storyIdea, "Saved exact idea");
  await s.select(1);
  await s.check(s.root.querySelector(".story-scopes").querySelector("input"));
  await s.type(s.find("instruction"), "Closer");
  s.api.reviseStoryPlan = async () => { throw new Error("Revision failed"); };
  await s.planner.reviseDraft();
  assert.equal(s.planner.storyIdea, "Saved exact idea");
  s.api.applyInitialRange = async () => { throw new Error("Apply failed"); };
  await s.planner.applyDraft();
  assert.equal(s.planner.storyIdea, "Saved exact idea");
  s.api.applyInitialRange = async () => ({ id: "project", storyIdea: "Saved exact idea" });
  await s.planner.applyDraft();
  assert.equal(s.planner.storyIdea, "Saved exact idea");
});

test("explicit 120 Story timeout is forwarded despite initial 600", async () => {
  const s = setup();
  assert.equal(s.find("timeout").value, "600");
  await s.type(s.find("timeout"), "120");
  await s.build();
  assert.equal(s.calls[0].body.llm.timeoutSeconds, 120);
});

test("planned references show inactive source filenames; existing items stay advisory", async () => {
  const response = proposal();
  const ref = { sourceGenerationId: "generation-a", assetId: "dog", recommendation: "Keep the dog consistent" };
  response.items[0].referenceRecommendations = [ref];
  response.items[1].referenceRecommendations = [ref];
  const s = setup({ response });
  s.context.assets = [{ id: "dog", filename: "dog.png", role: "subject_identity" }];
  await s.build();
  assert.match(s.cards()[0].textContent, /Recommendation · Source Generation 1 · dog.png · subject_identity. Advisory/);
  assert.match(s.cards()[1].textContent, /Planned reference · Source Generation 1 · dog.png · subject_identity. Will be attached on Apply/);
  assert.match(s.cards()[1].textContent, /Keep the dog consistent/);
});

function proposal() {
  return {
    baseProjectId: "project", baseProjectUpdatedAt: "before", baseSequenceFingerprint: "fingerprint",
    sequenceContext: "World",
    items: [0, 1, 2, 3].map(index => ({ planItemId: `item-${index}`, existingGenerationId: index === 0 ? "generation-a" : null,
      scenePlan: `Action ${index}`, incomingState: `Start ${index}`, intendedOutgoingState: `End ${index}`,
      dialogue: index === 1 ? "Alice: Wait.\nBob: No." : "",
      relationshipToPrevious: index === 0 ? null : "independent", cameraIntent: "Wide", referenceRecommendations: [] })),
    consistencyWarnings: [],
  };
}

function initialRangeProposal(count = 4) {
  return {
    sequenceContext: "World",
    items: Array.from({ length: count }, (_, index) => ({
      planItemId: `initial-item-${index}`, existingGenerationId: index === 0 ? "generation-a" : null,
      scenePlan: `Initial action ${index}`, incomingState: `Initial start ${index}`,
      intendedOutgoingState: `Initial end ${index}`, dialogue: index === 0 ? "Alice: Begin.\n" : "",
      relationshipToPrevious: index === 0 ? null : "independent", cameraIntent: "Wide",
      referenceRecommendations: [],
      memoryPatchAfter: index === 0
        ? { recentSummary: "Alice starts with the package.", factUpserts: [], threadUpserts: [] }
        : { recentSummary: `Alice reaches beat ${index + 1}.`, factUpserts: [], threadUpserts: [] },
    })),
    warnings: [],
    authority: { requestedTotalCount: count, g1GenerationId: "generation-a" }, authorityToken: "signed",
  };
}

function setup({ mountedRoot, generateStoryButton, response = proposal(), canonicalIdea = false, onSelectGeneration, onConfirmMemory, activity,
  modelSupportsVision = false, preferences = { timeoutSeconds: 600, reasoning: true, vision: false } } = {}) {
  const parent = doc.createElement("aside"); parent.className = "left-rail";
  const sequence = doc.createElement("section"); sequence.id = "sequence-panel"; parent.append(sequence);
  const root = mountedRoot || doc.createElement("div"); if (!mountedRoot) sequence.append(root);
  const context = { saved: true, dirty: false, projectId: "project", generations: [{ generationId: "generation-a" }], activeGenerationId: "generation-a" };
  const calls = [], prompts = [], adopted = [], applyBusy = [], preferenceChanges = [];
  let approved = true, supportsVision = modelSupportsVision;
  const api = {
    buildInitialRange: async (id, body) => { calls.push({ action: "build", id, body }); return { ...structuredClone(response), authority: {}, items: response.items.map(item => ({ ...structuredClone(item), memoryPatchAfter: {} })) }; },
    reviseStoryPlan: async (id, body) => { calls.push({ action: "revise", id, body }); return structuredClone(body.draft); },
    applyInitialRange: async (id, body) => { calls.push({ action: "apply", id, body }); return { id, sequence: { generations: body.draft.items } }; },
    applyStoryPlan: async (id, body) => { calls.push({ action: "apply", id, body }); return { id, sequence: { generations: body.draft.items } }; },
  };
  const planner = createStoryPlanner({ root, api, getContext: () => context, getSettings: () => ({ modelId: "fake", timeoutSeconds: 120,
    ...(supportsVision ? { supportsVision: true } : {}) }),
    getPreferences: () => preferences, onPreferencesChange: value => preferenceChanges.push(value),
    activity, generateStoryButton,
    onIdeaChange: () => { if (canonicalIdea) context.dirty = true; },
    onCreativeDirectionChange: () => { if (canonicalIdea) context.dirty = true; },
    onPlanningStyleChange: () => { context.dirty = true; },
    onMemoryChange: () => { context.dirty = true; },
    onScenePlanChange: value => { context.scenePlan = value; context.dirty = true; },
    onSelectGeneration, onConfirmMemory,
    adoptProject: project => { adopted.push(project); context.dirty = false; },
    confirmDiscard: text => { prompts.push(text); return approved; }, onApplyBusy: value => applyBusy.push(value),
  });
  const find = id => root.querySelector(`#story-${id}`);
  const cards = () => root.querySelectorAll("fieldset");
  const input = (index, field) => cards()[index].querySelectorAll("textarea, select").find(el => el.dataset.field === field);
  const type = async (el, value) => { el.value = value; await el.emit("input"); };
  const check = async (el, checked = true) => { el.checked = checked; await el.emit("change"); };
  const select = index => check(cards()[index].querySelector("input"));
  const ordinary = () => planner.restoreRevisionDraft(response);
  const build = async () => { await type(find("idea"), "A cyclist rescues a dog."); await planner.buildDraft(); };
  return { root, context, calls, prompts, adopted, applyBusy, preferenceChanges, api, planner, find, cards, input, type, check, select, build, ordinary,
    approve: value => { approved = value; },
    setModelSupportsVision: value => { supportsVision = value; planner.refresh(); } };
}

test("mounted Planning Style segmented controls preserve canonical state, dirty state, reload and reset", async () => {
  const s = setup();
  assert.deepEqual(s.planner.storyPlanningStyle, { creativity: "balanced", scenePlanDetail: "standard" });
  assert.equal(s.find("planning-style").open, false);
  const creativity = s.find("creativity-controls");
  const detail = s.find("scenePlanDetail-controls");
  assert.equal(creativity.querySelectorAll("select").length + detail.querySelectorAll("select").length, 0);
  const selected = group => group.children.filter(choice => choice.attributes["aria-pressed"] === "true");
  const choose = async (group, value) => {
    const choice = group.children.find(candidate => candidate.dataset.value === value);
    assert.ok(choice, `missing option ${value}`);
    await choice.emit("click");
    assert.equal(selected(group).length, 1);
  };
  assert.equal(selected(creativity)[0].dataset.value, "balanced");
  assert.equal(selected(detail)[0].dataset.value, "standard");
  assert.equal(s.find("planning-style").querySelectorAll("select").length, 0);
  assert.equal(s.context.dirty, false);
  await choose(creativity, "faithful");
  assert.deepEqual(s.planner.storyPlanningStyle, { creativity: "faithful", scenePlanDetail: "standard" });
  assert.equal(s.context.dirty, true);
  s.context.dirty = false;
  await choose(creativity, "exploratory");
  assert.deepEqual(s.planner.storyPlanningStyle, { creativity: "exploratory", scenePlanDetail: "standard" });
  assert.equal(s.context.dirty, true);
  s.context.dirty = false;
  await choose(detail, "concise");
  assert.deepEqual(s.planner.storyPlanningStyle, { creativity: "exploratory", scenePlanDetail: "concise" });
  s.context.dirty = false;
  await choose(detail, "detailed");
  assert.deepEqual(s.planner.storyPlanningStyle, { creativity: "exploratory", scenePlanDetail: "detailed" });
  assert.equal(s.context.dirty, true);
  const saved = await persistFilesystemProject({ projectId: "project", workspace: {},
    storyPlanningStyle: s.planner.storyPlanningStyle,
    saveProject: async (_id, _workspace, _continuity, _planning, _idea, _memory, _authority, _direction,
      _profiles, _language, style) => ({ storyPlanningStyle: structuredClone(style) }) });
  const reopened = setup();
  reopened.planner.restoreStoryPlanningStyle(saved.response.storyPlanningStyle);
  assert.deepEqual(reopened.planner.storyPlanningStyle, saved.response.storyPlanningStyle);
  assert.equal(selected(reopened.find("creativity-controls"))[0].dataset.value, "exploratory");
  assert.equal(selected(reopened.find("scenePlanDetail-controls"))[0].dataset.value, "detailed");
  reopened.context.activeGenerationId = "generation-b";
  reopened.planner.refresh();
  assert.deepEqual(reopened.planner.storyPlanningStyle, saved.response.storyPlanningStyle);
  await reopened.find("reset-planning-style").emit("click");
  assert.deepEqual(reopened.planner.storyPlanningStyle, { creativity: "balanced", scenePlanDetail: "standard" });
  assert.equal(selected(reopened.find("creativity-controls"))[0].dataset.value, "balanced");
  assert.equal(selected(reopened.find("scenePlanDetail-controls"))[0].dataset.value, "standard");
  assert.equal(reopened.context.dirty, true);
  assert.equal(reopened.find("planning-style").open, false);
});

test("Story running timer, duplicate guard, cancel, late response and retry", async () => {
  const oldNow = Date.now, oldInterval = globalThis.setInterval, oldClear = globalThis.clearInterval;
  let now = 0, tick, timers = 0;
  Date.now = () => now;
  globalThis.setInterval = callback => { tick = callback; timers++; return 1; };
  globalThis.clearInterval = () => { tick = null; };
  try {
    const activity = createProviderActivity();
    const s = setup({ activity }); s.planner.restoreStoryIdea("Saved story idea");
    const banner = doc.createElement("div");
    const bannerLabel = doc.createElement("span"); bannerLabel.id = "provider-activity-label";
    const bannerCancel = doc.createElement("button"); bannerCancel.id = "provider-activity-cancel";
    banner.append(bannerLabel, bannerCancel); mountProviderActivity(banner, activity);
    let finish, signal, calls = 0;
    s.api.buildInitialRange = (_id, _body, abortSignal) => { calls++; signal = abortSignal; return new Promise(resolve => { finish = resolve; }); };
    const pending = s.planner.buildDraft();
    assert.equal(s.root.querySelector("#story-running").hidden, false);
    assert.match(s.root.querySelector("#story-running").textContent, /Build Initial Range · 00:00/);
    assert.equal(s.root.querySelectorAll("#story-running").length, 1);
    assert.equal(s.root.querySelectorAll("#story-cancel").length, 1);
    assert.equal(s.root.querySelector("#story-running").parentNode, s.find("build").parentNode);
    assert.equal(s.find("applied-scene-plan").closest("#story-current-scene-plan").querySelector("#story-running"), null);
    now = 103000; tick();
    assert.match(s.root.querySelector("#story-running").textContent, /01:43/);
    assert.equal(timers, 1);
    assert.match(bannerLabel.textContent, /Build Initial Range · 01:43/);
    assert.notEqual(s.find("build").disabled, true);
    await s.planner.buildDraft();
    assert.equal(calls, 1);
    assert.match(s.find("status").textContent, /Build Initial Range is already running/);
    await s.find("cancel").emit("click");
    assert.equal(signal.aborted, true);
    assert.equal(activity.active(), null);
    assert.equal(banner.hidden, true);
    assert.equal(s.root.querySelector("#story-running").hidden, true);
    assert.match(s.find("status").textContent, /cancelled/);
    finish(proposal()); await pending;
    assert.equal(s.cards().length, 0);
    assert.notEqual(s.find("build").disabled, true);
    s.api.buildInitialRange = async () => { calls++; return { ...proposal(), items: proposal().items.map(item => ({ ...item, memoryPatchAfter: {} })) }; };
    await s.planner.buildDraft();
    assert.equal(calls, 2);
    assert.ok(s.cards().length > 0);
    assert.equal(s.root.querySelector("#story-running").hidden, true);
    await s.find("cancel").emit("click");
    assert.ok(s.cards().length > 0);
  } finally {
    Date.now = oldNow; globalThis.setInterval = oldInterval; globalThis.clearInterval = oldClear;
  }
});

test("shared activity blocks Story from a different provider operation and blocks H3 while Story runs", async () => {
  const activity = createProviderActivity();
  const s = setup({ activity }); s.planner.restoreStoryIdea("Saved story idea");
  const analysis = activity.begin({ id: "analyze-selected-take", label: "Selected Take", requestLabel: "Analyze Selected Take", verb: "Analyzing", owner: "rendered-takes", cancel: () => {} });
  await s.planner.buildDraft();
  assert.equal(s.calls.length, 0);
  assert.match(s.find("status").textContent, /Cannot start Build Initial Range while Analyze Selected Take/);
  activity.finish(analysis);
  const h3 = activity.begin({ id: "h3-story", label: "Generate from Story", owner: "app", cancel: () => {} });
  await s.planner.buildDraft();
  assert.match(s.find("status").textContent, /Cannot start Build Initial Range while Generate from Story/);
  activity.finish(h3);
  let resolve, calls = 0;
  s.api.buildInitialRange = () => { calls++; return new Promise(done => { resolve = done; }); };
  const pending = s.planner.buildDraft();
  assert.equal(activity.active().label, "Build Initial Range");
  assert.match(activity.conflict("h3-story", "Generate from Story"), /while Build Initial Range/);
  assert.equal(calls, 1);
  activity.cancelActive();
  assert.equal(activity.active(), null);
  resolve(initialRangeProposal()); await pending;
  assert.equal(s.cards().length, 0);
});

test("Story actions stay clickable and report H3 conflicts without API calls", async () => {
  const activity = createProviderActivity();
  const h3 = activity.begin({ id: "h3-main", label: "Generate from Main Prompt", owner: "app", cancel: () => {} });
  const s = setup({ activity });
  s.planner.restoreStoryIdea("Saved story idea");
  await s.find("build").emit("click");
  assert.equal(s.calls.length, 0);
  assert.notEqual(s.find("build").disabled, true);
  assert.match(s.find("status").textContent, /while Generate from Main Prompt/);
  activity.finish(h3);
  await s.find("build").emit("click");
  assert.equal(s.calls.length, 1);
});

for (const [label, make, action, buttonId] of [
  ["Plan Next", nextSetup, "planNextDraft", "next"],
  ["Insert Between", insertSetup, "planInsertDraft", "insert"],
  ["Replan Range", replanSetup, "planReplanDraft", "replan"],
]) {
  test(`${label} reports H3 conflict while its primary action remains clickable`, async () => {
    const activity = createProviderActivity();
    const h3 = activity.begin({ id: "h3-main", label: "Generate from Main Prompt", owner: "app", cancel: () => {} });
    const s = make({ activity });
    await s.find(buttonId).emit("click");
    assert.equal(s.calls.length, 0);
    assert.notEqual(s.find(buttonId).disabled, true);
    assert.match(s.find("status").textContent, /while Generate from Main Prompt/);
    assert.equal(s.planner.hasPendingRequest, false);
    activity.finish(h3);
  });
}

test("Story transport passes the active AbortSignal to fetch", async () => {
  const oldFetch = globalThis.fetch;
  const controller = new AbortController();
  let observed;
  globalThis.fetch = async (_url, options) => {
    observed = options.signal;
    return { ok: true, json: async () => ({ items: [] }) };
  };
  try {
    await realApi.buildInitialRange("project", { requestedTotalCount: 2 }, controller.signal);
    assert.equal(observed, controller.signal);
    controller.abort();
    assert.equal(observed.aborted, true);
  } finally { globalThis.fetch = oldFetch; }
});

test("Story Planner uses normalized peer sections and preserves Story Memory controls", async () => {
  const s = setup();
  const panel = s.root.querySelector("#story-memory");
  assert.ok(panel.className.split(" ").includes("story-planner-section"));
  assert.equal(panel.querySelector(".story-memory-lists").className, "story-memory-lists");
  const sections = s.root.children.filter(child => child.tagName === "details");
  assert.deepEqual(sections.map(section => section.id), ["story-build-section", "story-memory", "story-creative-direction", "story-planning-style", "story-next-section", "story-insert-section", "story-replan-section"]);
  assert.ok(sections.every(section => section.className.split(" ").includes("story-planner-section")));
  assert.ok(sections.every(section => section.open === false));
  assert.ok(!s.find("timeout").closest("details"));
  assert.ok(s.root.children.indexOf(s.find("timeout").parent) < s.root.children.indexOf(sections[0]));
  assert.equal(s.find("idea").closest("#story-build-section"), sections[0]);
  assert.equal(s.find("count").closest("#story-build-section"), sections[0]);
  assert.equal(s.find("creative-tone-mood").closest("#story-creative-direction"), sections[2]);
  assert.equal(s.find("creative-performance-energy").closest("#story-creative-direction"), sections[2]);
  assert.equal(s.find("creative-pacing-rhythm").closest("#story-creative-direction"), sections[2]);
  assert.equal(s.find("creative-beat-duration-density").closest("#story-creative-direction"), sections[2]);
  assert.equal(s.find("creativity-controls").closest("#story-planning-style"), sections[3]);
  assert.equal(s.find("scenePlanDetail-controls").closest("#story-planning-style"), sections[3]);
  assert.equal(s.find("next-count").closest("#story-next-section"), sections[4]);
  assert.equal(s.find("next-instruction").closest("#story-next-section"), sections[4]);
  assert.equal(s.find("insert-count").closest("#story-insert-section"), sections[5]);
  assert.equal(s.find("insert-instruction").closest("#story-insert-section"), sections[5]);
  assert.equal(s.find("replan-start").closest("#story-replan-section"), sections[6]);
  assert.equal(s.find("replan-end").closest("#story-replan-section"), sections[6]);
  assert.equal(s.find("replan-instruction").closest("#story-replan-section"), sections[6]);
  assert.equal(s.find("memory-summary").closest("#story-memory"), sections[1]);
  for (const key of ["durableFacts", "openThreads"]) {
    const add = s.find(`add-${key}`);
    assert.equal(add.closest(".story-memory-group-actions").className,
      "story-memory-action-row story-memory-group-actions");
    await add.emit("click");
    const entry = s.find("memory-" + s.planner.storyMemory[key][0].id);
    const finalActions = entry.closest(".story-memory-entry").querySelector(".story-memory-entry-actions");
    assert.equal(finalActions.className, "story-memory-action-row story-memory-entry-actions story-memory-group-actions");
    assert.equal(finalActions.querySelector(`#story-add-${key}`), s.find(`add-${key}`));
  }
});

test("LLM instruction preview selects path inside its dialog and defaults to current result", async () => {
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  const app = await readFile(new URL("../../web/js/app.js", import.meta.url), "utf8");
  const dialog = html.match(/<dialog id="instructions-dialog">([\s\S]*?)<\/dialog>/)?.[1] || "";
  const toolbar = html.match(/<div class="generate-bar">([\s\S]*?)<\/div>\s*<div id="performance-summary"/)?.[1] || "";
  assert.match(dialog, /id="instructions-path"/);
  assert.doesNotMatch(toolbar, /id="instructions-path"/);
  assert.match(html, /value="one_shot">Main Prompt instructions/);
  assert.match(html, /value="story">Story instructions/);
  assert.match(app, /const authoringPath = current\?\.authoringPath \|\| "one_shot"/);
  assert.match(app, /api\.assemble\(buildGenerateRequest\(authoringPath\)\)/);
  assert.ok(app.includes('$("#instructions-path").addEventListener("change"'));
});

test("applied Scene Plan stays editable and draft cards keep technical fields collapsed", async () => {
  const s = setup();
  s.planner.restoreScenePlan("Saved scene");
  assert.equal(s.find("applied-scene-plan").value, "Saved scene");
  await s.type(s.find("applied-scene-plan"), "Edited scene");
  assert.equal(s.context.scenePlan, "Edited scene");
  assert.equal(s.context.dirty, true);
  s.context.dirty = false;
  await s.build();
  assert.ok(s.input(0, "scenePlan"));
  assert.equal(s.input(0, "scenePlan").closest("details").id, "story-build-section");
  assert.ok(s.input(0, "incomingState").closest("details").className.includes("story-technical-details"));
});

test("Insert Between protects A/B, keeps first relationship editable, and owns its instruction lifecycle", async () => {
  const s = insertSetup();
  assert.equal(storyInsertEligible(s.context, 2), true);
  assert.equal(Boolean(s.find("insert").disabled), false);
  assert.match(s.find("insert-boundary").textContent, /Insert after: Generation 3/);
  assert.match(s.find("insert-boundary").textContent, /Protected next Generation: Generation 4/);
  await s.type(s.find("insert-instruction"), "Bridge the unresolved package delivery");
  await s.planner.planInsertDraft();
  assert.deepEqual(s.calls[0].body, { upstreamGenerationId: "g-3", requestedCount: 1,
    instruction: "Bridge the unresolved package delivery", llm: { modelId: "fake", timeoutSeconds: 600, reasoningMode: "on", storyPlannerVision: false } });
  assert.equal(s.cards().length, 2);
  assert.equal(s.cards()[0].closest(".story-insert-section"), s.find("insert").closest(".story-insert-section"));
  assert.equal(s.input(0, "relationshipToPrevious").disabled, false);
  assert.match(s.cards()[0].textContent, /Story Memory After This Generation/);
  s.input(0, "relationshipToPrevious").value = "independent";
  await s.input(0, "relationshipToPrevious").emit("change");
  await s.planner.applyDraft();
  assert.equal(s.calls[1].action, "insert-apply");
  assert.equal(s.calls[1].body.draft.slots[0].relationshipToPrevious, "independent");
  assert.equal(s.find("insert-instruction").value, "");

  await s.type(s.find("insert-instruction"), "Retain after failure");
  s.api.planStoryInsert = async () => { throw new Error("Planner unavailable"); };
  await s.planner.planInsertDraft();
  assert.equal(s.find("insert-instruction").value, "Retain after failure");
  s.api.planStoryInsert = async () => insertProposal();
  await s.planner.planInsertDraft(); await s.find("discard").emit("click");
  assert.equal(s.find("insert-instruction").value, "");

  s.context.generations[2].storyMemoryStatus = "needs_review"; s.planner.refresh();
  assert.equal(storyInsertEligible(s.context, 1), false);
  assert.equal(Boolean(s.find("insert").disabled), false);
  assert.match(s.find("insert-boundary").textContent, /blocked/);
  s.context.generations[2].storyMemoryStatus = "trusted"; s.context.activeGenerationId = "g-5"; s.planner.refresh();
  assert.equal(storyInsertEligible(s.context, 1), false);
});

test("Replan Range constrains contiguous selectors, keeps only target cards, and owns its instruction", async () => {
  const s = replanSetup();
  assert.equal(storyReplanEligible(s.context, "g-3", "g-8"), true);
  assert.equal(storyReplanEligible(s.context, "g-3", "g-2"), false);
  assert.equal(storyReplanEligible(s.context, "g-3", "g-9"), false);
  assert.equal(Boolean(s.find("replan").disabled), false);
  assert.equal(s.find("replan-end").querySelectorAll("option").length, 6);
  s.find("replan-end").value = "g-5"; await s.find("replan-end").emit("change");
  await s.type(s.find("replan-instruction"), "Make these beats more tense.");
  await s.planner.planReplanDraft();
  assert.deepEqual(s.calls[0].body, { startGenerationId: "g-3", endGenerationId: "g-5",
    instruction: "Make these beats more tense.", llm: { modelId: "fake", timeoutSeconds: 600, reasoningMode: "on", storyPlannerVision: false } });
  assert.equal(s.cards().length, 3);
  assert.equal(s.cards()[0].closest("#story-replan-section"), s.find("replan").closest("#story-replan-section"));
  assert.equal(s.input(0, "relationshipToPrevious").disabled, false);
  assert.match(s.cards()[0].textContent, /Story Memory After This Generation/);
  assert.match(s.cards()[0].textContent, /Alice approaches the intact bridge/);
  await s.planner.applyDraft();
  assert.equal(s.calls[1].action, "replan-apply");
  assert.equal(s.find("replan-instruction").value, "");

  await s.type(s.find("replan-instruction"), "Keep after failed request");
  s.api.planStoryReplan = async () => { throw new Error("Planner unavailable"); };
  await s.planner.planReplanDraft();
  assert.equal(s.find("replan-instruction").value, "Keep after failed request");
  s.api.planStoryReplan = async () => replanProposal();
  await s.planner.planReplanDraft(); await s.find("discard").emit("click");
  assert.equal(s.find("replan-instruction").value, "");

  s.context.generations[1].storyMemoryStatus = "needs_review"; s.planner.refresh();
  assert.equal(storyReplanEligible(s.context, "g-3", "g-3"), false);
  assert.equal(Boolean(s.find("replan").disabled), false);
  assert.match(s.find("replan-boundary").textContent, /blocked/);
});

test("Build Initial Range is a sole-G1 operation and drafts remain in their owning sections", async () => {
  const s = setup();
  const buildSection = s.find("build").closest("#story-build-section");
  assert.equal(buildSection.open, false);
  await s.type(s.find("idea"), "Idea");
  assert.equal(Boolean(s.find("build").disabled), false);
  s.context.generations.push({ generationId: "generation-b", storyMemoryStatus: "trusted" });
  s.context.activeGenerationId = "generation-b"; s.planner.refresh();
  assert.equal(Boolean(s.find("build").disabled), false);
  assert.equal(buildSection.querySelectorAll(".helper")[0].textContent, "Initial Range is only for a Project with one saved Generation. Use Replan Range to rewrite existing Story Generations, or Plan Next to continue.");
  s.context.activeGenerationId = "generation-a"; s.planner.refresh();
  assert.equal(Boolean(s.find("build").disabled), false);

  const next = nextSetup();
  await next.planner.planNextDraft();
  assert.equal(next.cards()[0].closest("#story-next-section"), next.find("next").closest("#story-next-section"));
  assert.equal(next.find("next").closest("#story-next-section").open, true);
  const insert = insertSetup();
  await insert.planner.planInsertDraft();
  assert.equal(insert.cards()[0].closest("#story-insert-section"), insert.find("insert").closest("#story-insert-section"));
  assert.equal(insert.find("insert").closest("#story-insert-section").open, true);
  const built = setup();
  await built.build();
  assert.equal(built.cards()[0].closest("#story-build-section"), built.find("build").closest("#story-build-section"));
  assert.equal(built.find("build").closest("#story-build-section").open, true);
});

test("mounted Story Planner gates unsaved/dirty Projects and validates the 2–12 initial range", async () => {
  const s = setup();
  await s.type(s.find("idea"), "Idea");
  for (const patch of [{ saved: false }, { saved: true, dirty: true }]) {
    Object.assign(s.context, patch); s.planner.refresh();
    assert.equal(Boolean(s.find("build").disabled), false);
    await s.planner.buildDraft();
  }
  assert.equal(s.calls.length, 0);
  s.context.dirty = false; s.planner.refresh();
  assert.equal(Boolean(s.find("build").disabled), false);
  for (const value of ["1", "13", "2.5", ""]) {
    await s.type(s.find("count"), value);
    assert.equal(Boolean(s.find("build").disabled), false);
  }
  await s.type(s.find("count"), "5");
  assert.equal(Boolean(s.find("build").disabled), false);
  assert.equal(s.find("extended-build-warning").hidden, false);
  assert.equal(s.find("extended-build-warning").textContent,
    "Extended builds use a larger single model request and may have weaker long-range consistency. Larger builds may need a higher Max output tokens setting and longer Story Planner timeout, especially with reasoning enabled. For tighter planning, use 2–4 and continue with Plan Next.");
  await s.type(s.find("count"), "12");
  assert.equal(Boolean(s.find("build").disabled), false);
  assert.equal(s.find("extended-build-warning").hidden, false);
  await s.type(s.find("count"), "4");
  assert.equal(s.find("extended-build-warning").hidden, true);
  s.context.generations = Array.from({ length: 5 }, (_, index) => ({ generationId: `g-${index + 1}`, storyMemoryStatus: "trusted" }));
  s.context.activeGenerationId = "g-1";
  await s.type(s.find("count"), "4"); assert.equal(Boolean(s.find("build").disabled), false);
});

test("Build serializes bounded TOTAL count and settings; adopts only the ephemeral draft", async () => {
  const s = setup(); await s.build();
  assert.deepEqual(s.calls[0], { action: "build", id: "project", body: {
    requestedTotalCount: 4, llm: { modelId: "fake", timeoutSeconds: 600, reasoningMode: "on", storyPlannerVision: false },
  } });
  assert.equal(s.cards().length, 4);
  assert.equal(s.adopted.length, 0);
  assert.equal(s.context.generations.length, 1);
  assert.equal(s.context.dirty, false);
  assert.equal(s.cards()[0].querySelector("legend").textContent, "Generation 1 · Existing Generation");
  assert.equal(s.cards()[3].querySelector("legend").textContent, "Generation 4 · New Generation");
  assert.equal(s.input(0, "relationshipToPrevious").disabled, true);
  assert.equal(s.input(1, "dialogue").value, "Alice: Wait.\nBob: No.");
  assert.equal(s.input(1, "dialogue").rows, 3);
  assert.equal(s.input(1, "relationshipToPrevious").querySelectorAll("option").length, 4);
  const scopeLabel = s.find("scope-camera_intent").parent;
  assert.equal(scopeLabel.children[0], s.find("scope-camera_intent"));
  assert.equal(scopeLabel.className, "story-scope-option");
});

test("incomplete Build fields remain editable and show a local planner warning", async () => {
  const response = proposal();
  response.items[0].scenePlan = "";
  response.consistencyWarnings = [{ planItemId: "item-0", relatedPlanItemId: "item-0", direction: "downstream",
    message: "Build left these planning fields incomplete: Creative Request. Edit the draft before Apply." }];
  const s = setup({ response }); await s.build();
  assert.equal(s.find("warnings").textContent, `Generation 1: ${response.consistencyWarnings[0].message}`);
  assert.equal(s.input(0, "scenePlan").disabled, false);
  await s.type(s.input(0, "scenePlan"), "The cyclist spots a dog.");
  await s.planner.applyDraft();
  assert.equal(s.calls.at(-1).body.draft.items[0].scenePlan, "The cyclist spots a dog.");
});

test("Story timeout accepts up to 3600 for Build and Revision", async () => {
  const s = setup();
  assert.equal(s.find("timeout").value, "600");
  await s.type(s.find("idea"), "Idea");
  for (const value of ["0", "3601", "", "invalid"]) {
    await s.type(s.find("timeout"), value);
    assert.equal(Boolean(s.find("build").disabled), false);
  }
  await s.type(s.find("timeout"), "360");
  await s.planner.buildDraft(); s.ordinary();
  assert.equal(s.calls[0].body.llm.timeoutSeconds, 360);
  await s.select(1); await s.check(s.find("scope-camera_intent"));
  await s.type(s.find("instruction"), "A wider shot");
  await s.planner.reviseDraft();
  assert.equal(s.calls.at(-1).body.llm.timeoutSeconds, 360);
});

test("Build Initial Range uses the bounded transport and shows G1→GN memory previews", async () => {
  const s = setup();
  s.api.buildInitialRange = async (id, body) => {
    s.calls.push({ action: "initial-range", id, body }); return initialRangeProposal(body.requestedTotalCount);
  };
  s.api.applyInitialRange = async (id, body) => {
    s.calls.push({ action: "initial-range-apply", id, body }); return { id, sequence: { generations: body.draft.items } };
  };
  await s.type(s.find("idea"), "A bounded beginning.");
  await s.planner.buildDraft();
  assert.deepEqual(s.calls[0], { action: "initial-range", id: "project", body: {
    requestedTotalCount: 4, llm: { modelId: "fake", timeoutSeconds: 600, reasoningMode: "on", storyPlannerVision: false },
  } });
  assert.equal(s.cards().length, 4);
  assert.match(s.cards()[0].querySelector("legend").textContent, /Existing Generation/);
  assert.match(s.cards()[3].querySelector("legend").textContent, /New Generation/);
  assert.match(s.cards()[0].textContent, /Story Memory After This Generation/);
  assert.match(s.cards()[0].textContent, /Alice starts with the package/);
  assert.match(s.cards()[3].textContent, /Alice reaches beat 4/);
  assert.equal(s.find("revise").parent.hidden, true);
  await s.planner.applyDraft();
  assert.equal(s.calls[1].action, "initial-range-apply");

  await s.type(s.find("count"), "2");
  await s.planner.buildDraft();
  assert.equal(s.calls[2].body.requestedTotalCount, 2);
  assert.equal(s.cards().length, 2);

  await s.type(s.find("count"), "12");
  assert.equal(s.find("extended-build-warning").hidden, false);
  await s.planner.buildDraft();
  assert.equal(s.calls[3].body.requestedTotalCount, 12);
  assert.equal(s.cards().length, 12);
  assert.match(s.cards()[11].querySelector("legend").textContent, /Generation 12 · New Generation/);
});

test("Initial Range conflict detail stays visible without an automatic retry", async () => {
  const s = setup();
  let calls = 0;
  const detail = "Story authority used by Build Initial Range changed while planning; review saved Story fields and try again";
  s.api.buildInitialRange = async () => { calls += 1; throw new Error(detail); };
  await s.type(s.find("idea"), "A bounded beginning.");
  await s.planner.buildDraft();
  assert.equal(calls, 1);
  assert.equal(s.find("status").textContent, detail);
});

test("new Story cards edit bounded visual Subject participation and send it on Apply", async () => {
  const s = nextSetup();
  s.context.subjects = [
    { id: "kent", number: 1, narrativeName: "Kent" },
    { id: "alice", number: 2, narrativeName: "Alice" },
    { id: "bob", number: 3, narrativeName: "Bob" },
  ];
  s.api.planStoryNext = async (id, body) => {
    s.calls.push({ action: "next", id, body });
    const result = nextProposal();
    result.authority.allowedSubjectIds = ["kent", "alice"];
    result.slots[0].participatingSubjectIds = ["kent"];
    return result;
  };
  await s.planner.planNextDraft();
  const card = s.cards()[0];
  const participation = card.querySelector(".story-subject-participation");
  assert.ok(participation);
  assert.match(participation.textContent, /Kent \(Subject 1\)/);
  assert.doesNotMatch(participation.textContent, /Bob/);
  const boxes = participation.querySelectorAll("input");
  assert.equal(boxes.length, 2);
  assert.equal(boxes[0].checked, true);
  assert.equal(boxes[1].checked, false);
  await s.check(boxes[1], true);
  await s.planner.applyDraft();
  assert.deepEqual(s.calls.at(-1).body.draft.slots[0].participatingSubjectIds, ["kent", "alice"]);

  const existing = setup({ response: initialRangeProposal(2) });
  existing.context.subjects = s.context.subjects;
  existing.ordinary();
  assert.equal(existing.cards()[0].querySelector(".story-subject-participation"), null);
  assert.ok(existing.cards()[1].querySelector(".story-subject-participation"));
});

test("Story drafts do not edit H3 Tone / Performance or expose a Revision scope", async () => {
  const response = initialRangeProposal();
  response.items[1].tonePerformance = { drama: "strong" }; // Old replies remain harmless.
  const s = setup({ response });
  await s.build();
  assert.equal(s.cards()[1].querySelector(".story-tone-performance-controls"), null);
  assert.equal(s.find("scope-tone_performance_creative_controls"), null);
  await s.planner.applyDraft();
  assert.equal(s.calls.at(-1).body.draft.items[1].tonePerformance, undefined);
});

test("Story Planner reasoning is independent and wired through every operation", async () => {
  const h3Off = { modelId: "fake", timeoutSeconds: 120, reasoningMode: "off", maxTokens: 4096 };
  const h3On = { ...h3Off, reasoningMode: "on" };
  assert.equal(storyPlannerSettings(h3Off, { timeoutSeconds: 600, reasoning: true }).reasoningMode, "on");
  assert.equal(storyPlannerSettings(h3On, { timeoutSeconds: 600, reasoning: false }).reasoningMode, "off");
  assert.equal(h3Off.reasoningMode, "off");
  assert.equal(h3On.reasoningMode, "on");

  const build = setup();
  assert.equal(build.find("reasoning").checked, true);
  await build.build(); build.ordinary();
  await build.select(1); await build.check(build.find("scope-camera_intent")); await build.type(build.find("instruction"), "Widen it");
  await build.planner.reviseDraft();
  assert.equal(build.calls[0].body.llm.reasoningMode, "on");
  assert.equal(build.calls[1].body.llm.reasoningMode, "on");

  const next = nextSetup(); await next.planner.planNextDraft();
  const insert = insertSetup(); await insert.planner.planInsertDraft();
  const replan = replanSetup(); await replan.planner.planReplanDraft();
  for (const request of [next.calls[0], insert.calls[0], replan.calls[0]]) assert.equal(request.body.llm.reasoningMode, "on");

  const off = setup(); await off.check(off.find("reasoning"), false); await off.build();
  assert.equal(off.calls[0].body.llm.reasoningMode, "off");
});

test("mounted Story Planner Vision and reasoning switches keep independent saved preferences", async () => {
  const s = setup({ modelSupportsVision: true });
  const vision = s.find("vision"), reasoning = s.find("reasoning");
  for (const input of [vision, reasoning]) {
    assert.equal(input.type, "checkbox");
    assert.equal(input.attributes.role, "switch");
    assert.ok(input.attributes["aria-label"].startsWith("Story Planner"));
    assert.equal(input.disabled, false);
    const toggle = input.parent, group = toggle.parent;
    assert.equal(toggle.className, "story-planner-toggle");
    assert.equal(group.className, "story-planner-preference-item");
    assert.equal(group.children[0].className, "story-planner-preference-title");
    assert.equal(group.children[0].textContent, input.attributes["aria-label"]);
    assert.equal(group.children[0].attributes.for, input.id);
    assert.equal(group.children[1], toggle);
    assert.deepEqual(toggle.children.map(child => child.className),
      ["story-planner-toggle-input", "story-planner-toggle-track", "story-planner-toggle-state"]);
    assert.equal(toggle.querySelectorAll("input").length, 1);
    assert.equal(group.querySelectorAll(".story-planner-preference-title").length, 1);
    assert.equal(group.querySelectorAll(".story-planner-toggle-track").length, 1);
    assert.equal(group.querySelectorAll(".story-planner-toggle-state").length, 1);
    assert.equal(toggle.querySelector(".story-planner-preference-title"), null);
    assert.equal(toggle.querySelector(".helper"), null);
  }
  const visionGroup = vision.parent.parent;
  assert.ok(visionGroup.children.includes(visionGroup.querySelector(".story-planner-vision-help")));
  assert.equal(reasoning.parent.parent.querySelector(".story-planner-vision-help"), null);
  const preferences = visionGroup.parent;
  assert.equal(preferences.className, "story-planner-preferences");
  assert.equal(preferences.children.filter(child => child.className === "story-planner-preference-item").length, 2);
  assert.equal(vision.checked, false);
  assert.equal(reasoning.checked, true);
  await s.check(vision, true);
  assert.equal(s.preferenceChanges.at(-1).vision, true);
  assert.equal(vision.parent.querySelector(".story-planner-toggle-state").textContent, "On");
  await s.check(reasoning, false);
  assert.equal(s.preferenceChanges.at(-1).reasoning, false);
  await s.build();
  assert.equal(s.calls[0].body.llm.storyPlannerVision, true);
  assert.equal(s.calls[0].body.llm.reasoningMode, "off");
  assert.equal(vision.disabled, false);
  assert.equal(reasoning.disabled, false);
  s.planner.restorePreferences({ timeoutSeconds: 600, reasoning: true, vision: false });
  assert.equal(vision.checked, false);
  assert.equal(reasoning.checked, true);

  const incapable = setup();
  assert.equal(incapable.find("vision").checked, false);
  assert.equal(incapable.find("vision").disabled, false);
  assert.equal(incapable.find("reasoning").disabled, false);
  await incapable.check(incapable.find("vision"), true);
  await incapable.check(incapable.find("reasoning"), false);
  const saved = incapable.preferenceChanges.at(-1);
  assert.equal(saved.vision, true);
  assert.equal(saved.reasoning, false);
  assert.equal(incapable.find("vision").parent.querySelector(".story-planner-toggle-state").textContent, "On");
  assert.match(incapable.find("vision").parent.parent.querySelector(".story-planner-vision-help").textContent,
    /enabled, but inactive until "Model accepts images" is enabled/);
  await incapable.build();
  assert.equal(incapable.calls[0].body.llm.storyPlannerVision, true);
  assert.equal(incapable.calls[0].body.llm.reasoningMode, "off");
  assert.notEqual(incapable.calls[0].body.llm.supportsVision, true);
  assert.equal(incapable.find("vision").disabled, false);
  incapable.setModelSupportsVision(true);
  incapable.planner.restorePreferences(saved);
  assert.equal(incapable.find("vision").checked, true);
  await incapable.build();
  assert.equal(incapable.calls[1].body.llm.storyPlannerVision, true);
  assert.equal(incapable.calls[1].body.llm.supportsVision, true);
});

test("Story Planner preference container has responsive grid and stacked narrow layout", async () => {
  const css = await readFile(new URL("../../web/css/app.css", import.meta.url), "utf8");
  assert.match(css, /\.story-planner-preferences\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);[^}]*width:\s*min\(100%,\s*32rem\)/);
  assert.match(css, /\.story-planner-toggle\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*2\.35rem 1\.8rem;[^}]*width:\s*max-content/);
  assert.match(css, /\.story-planner-toggle-input\s*\{[^}]*width:\s*1px;[^}]*height:\s*1px;[^}]*padding:\s*0/);
  assert.doesNotMatch(css, /\.story-planner-toggle-input\s*\{[^}]*(?:inset:\s*0|width:\s*100%|height:\s*100%)/);
});

test("manual edits retain textarea identity, caret and focus; revision uses the latest full draft", async () => {
  const s = setup(); await s.build(); s.ordinary();
  const textarea = s.input(1, "scenePlan");
  textarea.selectionStart = 7; textarea.selectionEnd = 7; doc.activeElement = textarea;
  await s.type(textarea, "Manual text\n  🙂");
  await s.type(s.find("context"), "Manual world");
  assert.equal(Boolean(s.find("apply").disabled), false);
  assert.equal(s.input(1, "scenePlan"), textarea);
  assert.equal(textarea.selectionStart, 7);
  assert.equal(doc.activeElement, textarea);
  assert.equal(shouldMarkWorkspaceDirty(textarea), false);
  assert.equal(shouldMarkWorkspaceDirty(s.find("context")), false);
  assert.equal(s.context.dirty, false);
  await s.select(1); await s.select(2);
  assert.equal(s.root.querySelectorAll("input").filter(input => input.id?.startsWith("story-scope-")).length, 6);
  await s.check(s.find("scope-camera_intent"));
  await s.type(s.find("instruction"), "More dynamic cameras only");
  assert.equal(Boolean(s.find("revise").disabled), false);
  s.api.reviseStoryPlan = async (id, body) => {
    s.calls.push({ action: "revise", id, body });
    const result = structuredClone(body.draft);
    result.items[1].cameraIntent = "Track"; result.items[2].cameraIntent = "Low";
    return result;
  };
  await s.planner.reviseDraft();
  const request = s.calls.at(-1).body;
  assert.deepEqual(request.selectedPlanItemIds, ["item-1", "item-2"]);
  assert.deepEqual(request.scopes, ["camera_intent"]);
  assert.equal(request.draft.items[1].scenePlan, "Manual text\n  🙂");
  assert.equal(request.draft.sequenceContext, "Manual world");
  assert.equal(s.input(1, "scenePlan").value, "Manual text\n  🙂");
  assert.equal(s.input(1, "cameraIntent").value, "Track");
  assert.equal(s.input(0, "cameraIntent").value, "Wide");
  assert.equal(s.context.dirty, false);
});

test("Story cards expose editable shot count and multiline Shot Plan without changing H3 before Apply", async () => {
  const s = setup(); await s.build(); s.ordinary();
  const shots = s.cards()[1].querySelectorAll("input").find(el => el.dataset.field === "shotCount");
  assert.equal(shots.type, "number");
  assert.equal(shots.min, "1");
  assert.match(s.input(1, "cameraIntent").parent.textContent, /Shot Plan \/ Camera Intent/);
  await s.type(shots, "3");
  await s.type(s.input(1, "cameraIntent"), "Shot 1 — hold.\nShot 2 — cut.\nShot 3 — settle.");
  assert.equal(s.context.dirty, false);
  assert.equal(Boolean(s.find("apply").disabled), false);
  assert.equal(s.input(1, "cameraIntent").value.split("\n").length, 3);
  await s.planner.applyDraft();
  assert.equal(s.calls.at(-1).body.draft.items[1].shotCount, 3);
});

test("all ordinary draft prose edits and edit-revert keep Apply available", async () => {
  const s = setup(); await s.build(); s.ordinary();
  for (const fieldName of ["scenePlan", "incomingState", "intendedOutgoingState", "cameraIntent", "dialogue"]) {
    const input = s.input(1, fieldName), original = input.value;
    await s.type(input, `${original} edited`);
    assert.equal(Boolean(s.find("apply").disabled), false, fieldName);
    await s.type(input, original);
    assert.equal(Boolean(s.find("apply").disabled), false, `${fieldName} revert`);
  }
  const originalContext = s.find("context").value;
  await s.type(s.find("context"), "Temporary context edit");
  assert.equal(Boolean(s.find("apply").disabled), false);
  await s.type(s.find("context"), originalContext);
  assert.equal(Boolean(s.find("apply").disabled), false);
  assert.equal(s.planner.hasDraft, true);
});

test("Exact Dialogue / Lyrics supports multiline draft editing, blank silence, and an explicit revision scope", async () => {
  const s = setup(); await s.build(); s.ordinary();
  const dialogue = s.input(1, "dialogue");
  await s.type(dialogue, "Alice: Wait.\nBob: No.");
  await s.select(1); await s.check(s.find("scope-dialogue"));
  await s.type(s.find("instruction"), "Make this silent.");
  s.api.reviseStoryPlan = async (id, body) => {
    s.calls.push({ action: "revise", id, body });
    const result = structuredClone(body.draft); result.items[1].dialogue = ""; return result;
  };
  await s.planner.reviseDraft();
  assert.deepEqual(s.calls.at(-1).body.scopes, ["dialogue"]);
  assert.equal(s.input(1, "dialogue").value, "");
  await s.planner.applyDraft();
  assert.equal(s.calls.at(-1).body.draft.items[1].dialogue, "");
  assert.match(s.root.textContent, /Leave it blank for no exact dialogue requirement/);
});

test("revision failure preserves controls, manual values, selection and draft", async () => {
  const s = setup(); await s.build(); s.ordinary();
  await s.select(1); await s.check(s.find("scope-camera_intent"));
  await s.type(s.find("instruction"), "Change camera");
  const textarea = s.input(2, "scenePlan");
  await s.type(textarea, "Do not touch");
  s.api.reviseStoryPlan = async () => { throw new Error("Model changed draft identity"); };
  await s.planner.reviseDraft();
  assert.equal(s.input(2, "scenePlan"), textarea);
  assert.equal(textarea.value, "Do not touch");
  assert.equal(s.cards()[1].querySelector("input").checked, true);
  assert.equal(s.planner.hasDraft, true);
  assert.match(s.find("action-status").textContent, /identity/);
});

test("warnings and source-qualified recommendations render as literal text", async () => {
  const response = proposal(); response.items[1].existingGenerationId = "generation-b";
  const malicious = '<img src=x onerror="alert(1)">';
  response.items[2].referenceRecommendations = [
    { sourceGenerationId: "generation-a", assetId: "shared", recommendation: malicious },
    { sourceGenerationId: "generation-b", assetId: "shared", recommendation: "Other source" },
    { sourceGenerationId: "unknown", assetId: "bad", recommendation: "Ignore" },
    { sourceGenerationId: null, assetId: "bad", recommendation: "Ignore new slot" },
    null,
  ];
  response.consistencyWarnings = [{ planItemId: "item-1", relatedPlanItemId: "item-2", direction: "downstream", message: malicious }];
  const s = setup({ response }); s.ordinary();
  assert.match(s.root.textContent, /Source Generation 1 · shared/);
  assert.match(s.root.textContent, /Source Generation 2 · shared/);
  assert.ok(s.root.textContent.includes(malicious));
  assert.ok(!s.root.textContent.includes("Ignore"));
  assert.equal(s.root.querySelectorAll("img").length, 0);
  assert.match(s.find("warnings").textContent, /Generation 2 → Generation 3 · downstream/);
});

test("recommendations use only matching source metadata and keep pair identity in requests", async () => {
  const response = proposal(); response.items[1].existingGenerationId = "generation-b";
  const refs = [
    { sourceGenerationId: "generation-a", assetId: "shared", recommendation: "Use this view" },
    { sourceGenerationId: "generation-b", assetId: "shared", recommendation: "Other source" },
    { sourceGenerationId: "generation-a", assetId: "missing", recommendation: "Metadata unavailable" },
    { sourceGenerationId: "generation-a", assetId: "literal", recommendation: "Literal filename" },
  ];
  response.items[2].referenceRecommendations = refs;
  const s = setup({ response });
  s.context.activeGenerationId = "generation-a";
  s.context.assets = [
    { id: "shared", filename: "C:\\private\\station_environment.png", role: "environment", path: "assets/pictures/blob.png" },
    { id: "literal", filename: "<img src=x onerror=alert(1)>", role: "general_visual" },
  ];
  s.ordinary();
  const rendered = s.cards()[2].querySelector(".story-recommendations").textContent;
  assert.match(rendered, /Source Generation 1 · station_environment.png · environment. Will be attached on Apply. Use this view/);
  assert.match(rendered, /Source Generation 2 · shared. Will be attached on Apply. Other source/);
  assert.match(rendered, /Source Generation 1 · missing. Will be attached on Apply. Metadata unavailable/);
  assert.ok(rendered.includes("<img src=x onerror=alert(1)>"));
  assert.doesNotMatch(rendered, /private|assets\/pictures|blob.png/);
  assert.equal(s.root.querySelectorAll("img").length, 0);
  await s.planner.applyDraft();
  assert.deepEqual(s.calls.at(-1).body.draft.items[2].referenceRecommendations, refs);
});

test("revision scopes form a named group and status messages have live semantics", async () => {
  const s = setup(); await s.build();
  const group = s.root.querySelector(".story-scope-group");
  assert.equal(group.attributes.role, "group");
  const heading = s.root.querySelector(`#${group.attributes["aria-labelledby"]}`);
  assert.equal(heading.tagName, "h3");
  assert.equal(heading.textContent, "Fields to revise");
  for (const checkbox of group.querySelectorAll("input")) {
    assert.equal(checkbox.parent.tagName, "label");
    assert.ok(checkbox.parent.textContent.trim());
  }
  for (const id of ["status", "action-status"]) {
    assert.equal(s.find(id).attributes.role, "status");
    assert.equal(s.find(id).attributes["aria-live"], "polite");
  }
});

test("Apply failure preserves draft; success adopts one canonical response and clears draft", async () => {
  const s = setup(); await s.build();
  const originalApply = s.api.applyInitialRange;
  s.api.applyInitialRange = async () => { throw new Error("Underlying Project changed; rebuild draft"); };
  await s.planner.applyDraft();
  assert.equal(s.planner.hasDraft, true); assert.equal(s.adopted.length, 0);
  assert.match(s.find("action-status").textContent, /rebuild/);
  s.api.applyInitialRange = originalApply;
  await s.planner.applyDraft();
  assert.equal(s.adopted.length, 1); assert.equal(s.adopted[0].sequence.generations.length, 4);
  assert.equal(s.planner.hasDraft, false); assert.equal(s.cards().length, 0);
  assert.equal(s.context.dirty, false);
  assert.deepEqual(s.applyBusy, [true, false, true, false]);
  assert.deepEqual(s.calls.map(c => c.action), ["build", "apply"]);
  assert.equal(s.find("idea").value, "A cyclist rescues a dog.");
});

test("dirty canonical edits keep Apply attemptable but make no backend call and preserve both states", async () => {
  const s = setup(); await s.build();
  s.context.dirty = true; s.context.scenePlan = "Unsaved browser edit"; s.planner.refresh();
  assert.equal(Boolean(s.find("apply").disabled), false); assert.equal(Boolean(s.find("revise").disabled), false);
  let applyCalls = 0;
  s.api.applyInitialRange = async () => { applyCalls++; throw new Error("Should not reach backend."); };
  await s.find("apply").emit("click");
  assert.equal(applyCalls, 0); assert.equal(s.adopted.length, 0);
  assert.match(s.find("action-status").textContent, /Save or discard the current Project edits/);
  assert.equal(s.planner.hasDraft, true);
  assert.equal(s.context.dirty, true); assert.equal(s.context.scenePlan, "Unsaved browser edit");
  assert.equal(s.context.generations.length, 1);
});

test("completed Initial Range remains attemptable and a failed Apply retains the expensive draft", async () => {
  const s = setup();
  await s.build();
  s.planner.refresh();
  const apply = s.find("apply");
  assert.equal(Boolean(apply.disabled), false);
  assert.equal(apply.attributes["aria-disabled"], undefined);
  let attempts = 0;
  s.api.applyInitialRange = async () => { attempts++; throw new Error("Proposal authority changed (422)."); };
  await apply.emit("click");
  assert.equal(attempts, 1);
  assert.match(s.find("action-status").textContent, /422/);
  assert.equal(s.planner.hasDraft, true);
  assert.ok(s.cards().length > 0);
});

test("pending Initial Range stays clickable and rejects a duplicate provider request", async () => {
  const s = setup();
  await s.type(s.find("idea"), "A saved premise");
  let finish, calls = 0;
  s.api.buildInitialRange = async () => { calls++; return new Promise(resolve => { finish = resolve; }); };
  const pending = s.planner.buildDraft();
  assert.equal(Boolean(s.find("build").disabled), false);
  await s.find("build").emit("click");
  assert.equal(calls, 1);
  assert.match(s.find("status").textContent, /Build Initial Range is already running/);
  finish(initialRangeProposal()); await pending;
  assert.equal(Boolean(s.find("apply").disabled), false);
});

test("pending Replan stays clickable and rejects a duplicate provider request", async () => {
  const s = replanSetup();
  let finish, calls = 0;
  s.api.planStoryReplan = async () => { calls++; return new Promise(resolve => { finish = resolve; }); };
  const pending = s.planner.planReplanDraft();
  assert.equal(Boolean(s.find("replan").disabled), false);
  await s.find("replan").emit("click");
  assert.equal(calls, 1);
  assert.match(s.find("status").textContent, /Replan Range is already running/);
  finish(replanProposal()); await pending;
});

test("action failures stay beside the action and successful revision replaces stale error text", async () => {
  const s = setup(); await s.build(); s.ordinary();
  await s.select(1); await s.check(s.find("scope-camera_intent"));
  await s.type(s.find("instruction"), "Change camera");
  s.api.reviseStoryPlan = async () => { throw new Error("Project changed; rebuild or reconcile this draft"); };
  await s.planner.reviseDraft();
  assert.match(s.find("action-status").textContent, /Project changed/);
  assert.equal(s.cards().length, 4);
  s.api.reviseStoryPlan = async (_id, body) => structuredClone(body.draft);
  await s.planner.reviseDraft();
  assert.match(s.find("action-status").textContent, /Revision applied/);
  assert.doesNotMatch(s.find("action-status").textContent, /Project changed/);
});

test("same-Project discard keeps the idea, while context reset clears idea and transient revision state", async () => {
  const s = setup(); await s.build(); await s.type(s.find("idea"), "Keep this story idea");
  assert.equal(s.context.dirty, false);
  await s.select(1); await s.check(s.find("scope-camera_intent"));
  await s.type(s.find("instruction"), "Temporary instruction");
  await s.find("discard").emit("click");
  assert.equal(s.find("idea").value, "Keep this story idea");
  assert.equal(s.find("instruction").value, "");
  assert.equal(s.find("scope-camera_intent").checked, false);
  assert.equal(s.context.dirty, false);
  await s.type(s.find("idea"), "Another project idea"); await s.build();
  await s.select(1); await s.type(s.find("instruction"), "Should clear");
  s.planner.resetProjectContext();
  assert.equal(s.find("idea").value, "");
  assert.equal(s.find("instruction").value, "");
  assert.equal(s.find("scope-camera_intent").checked, false);
  assert.equal(s.planner.hasDraft, false);
  assert.equal(s.context.dirty, false);
});

test("same-Project Generation or Sequence changes clear the proposal but preserve the idea", async () => {
  const s = setup(); await s.build(); await s.type(s.find("idea"), "Sequence-wide idea");
  await s.select(1); await s.check(s.find("scope-camera_intent"));
  await s.type(s.find("instruction"), "Temporary revision");
  s.planner.clearDraft(); // app.js uses this after a same-Project Generation switch
  assert.equal(s.find("idea").value, "Sequence-wide idea");
  assert.equal(s.planner.hasDraft, false);
  assert.equal(s.find("instruction").value, "");
  assert.equal(s.find("scope-camera_intent").checked, false);
  await s.build(); await s.type(s.find("idea"), "Sequence-wide idea");
  s.planner.clearDraft(); // app.js uses this after a structural Sequence operation
  assert.equal(s.find("idea").value, "Sequence-wide idea");
  assert.equal(s.planner.hasDraft, false);
});

test("canceling a context prompt preserves idea and draft", async () => {
  const s = setup(); await s.build(); await s.type(s.find("idea"), "Keep on cancel");
  s.approve(false);
  assert.equal(s.planner.confirmContextChange({ message: "Open another Project?" }), false);
  assert.equal(s.find("idea").value, "Keep on cancel");
  assert.equal(s.planner.hasDraft, true);
  assert.equal(s.cards().length, 4);
});

test("conflicting navigation asks once; Cancel preserves all; approved discard clears only draft", async () => {
  const s = setup(); await s.build();
  s.context.dirty = true; s.approve(false);
  assert.equal(s.planner.confirmContextChange({ dirty: true, message: "Open another Project?" }), false);
  assert.equal(s.prompts.length, 1);
  assert.match(s.prompts[0], /Story Planner.*and unsaved Project/);
  assert.equal(s.planner.hasDraft, true); assert.equal(s.cards().length, 4); assert.equal(s.context.dirty, true);
  s.approve(true);
  assert.equal(s.planner.confirmContextChange({ dirty: true, message: "Open another Project?" }), true);
  assert.equal(s.prompts.length, 2); assert.equal(s.planner.hasDraft, false);
  assert.equal(s.context.dirty, true); assert.equal(s.context.generations.length, 1);
  assert.equal(s.planner.confirmContextChange(), true); assert.equal(s.prompts.length, 2);
});

test("discard while Build is pending invalidates its late response", async () => {
  const s = setup(); let finish;
  s.api.buildInitialRange = () => new Promise(resolve => { finish = resolve; });
  const pending = s.build();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(s.planner.hasDraft, true);
  assert.equal(s.planner.confirmContextChange({ message: "Switch Generation?" }), false);
  await s.find("discard").emit("click");
  finish(initialRangeProposal()); await pending;
  assert.equal(s.planner.hasDraft, false); assert.equal(s.cards().length, 0);
});

test("Project reset invalidates pending Build even before the Project ID changes", async () => {
  const s = setup(); let finish;
  s.api.buildInitialRange = () => new Promise(resolve => { finish = resolve; });
  const pending = s.build();
  await new Promise(resolve => setImmediate(resolve));
  s.planner.resetProjectContext();
  assert.equal(s.find("idea").value, "");
  finish(initialRangeProposal()); await pending;
  assert.equal(s.planner.hasDraft, false);
  assert.equal(s.cards().length, 0);
  assert.equal(s.find("status").textContent, "");
});

test("Apply in flight prevents context change until its outcome is known", async () => {
  const s = setup(); await s.build(); let finish;
  s.api.applyInitialRange = () => new Promise(resolve => { finish = resolve; });
  const pending = s.planner.applyDraft();
  assert.equal(s.planner.confirmContextChange(), false);
  assert.equal(s.prompts.length, 0);
  finish({ id: "project" }); await pending;
  assert.equal(s.adopted.length, 1);
});

test("Build failure stays visible and a previous draft survives", async () => {
  const s = setup(); await s.build();
  s.api.buildInitialRange = async () => { throw new Error("Invalid model structure"); };
  await s.planner.buildDraft();
  assert.equal(s.cards().length, 4); assert.match(s.find("status").textContent, /Invalid model structure/);
  assert.equal(s.root.querySelector("#story-running").hidden, true);
  await s.find("discard").emit("click");
  await s.planner.buildDraft();
  assert.match(s.find("status").textContent, /Invalid model structure/);
});

test("Story API sends one structured POST per phase and surfaces server conflicts", async () => {
  const calls = [], original = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options }); return { ok: calls.length < 3, status: 409, json: async () => ({ detail: "Project changed" }) };
  };
  try {
    await realApi.buildStoryPlan("a/b", { idea: "Idea", desiredGenerationCount: 4 });
    await realApi.reviseStoryPlan("a/b", { draft: proposal(), selectedPlanItemIds: ["item-1"] });
    await assert.rejects(realApi.applyStoryPlan("a/b", { draft: proposal() }), /Project changed/);
  } finally { globalThis.fetch = original; }
  assert.deepEqual(calls.map(c => c.url), ["build", "revise", "apply"].map(action => `/api/projects/a%2Fb/story-plan/${action}`));
  assert.ok(calls.every(c => c.options.method === "POST"));
  assert.equal(JSON.parse(calls[1].options.body).draft.items.length, 4);
});

test("rejected Story response is inspectable, copyable, and replaced by the next Story operation", async () => {
  const originalNavigator = globalThis.navigator;
  let copied = null;
  Object.defineProperty(globalThis, "navigator", {
    configurable: true, value: { clipboard: { writeText: async text => { copied = text; } } },
  });
  try {
    const s = setup();
    const raw = '```json\n{"items":[{"memoryPatchAfter":{"factUpserts":[{"relatedSubjectIds":[\nes"subject-id"]}]}}]}\n```';
    s.api.buildInitialRange = async () => { throw Object.assign(new Error("Story Planner returned malformed JSON at line 2, column 1."), { rawResponse: raw }); };
    await s.build();
    assert.equal(s.find("view-raw-response").hidden, false);
    assert.match(s.find("status").textContent, /malformed JSON/);
    await s.find("view-raw-response").emit("click");
    assert.equal(s.find("raw-response-dialog").open, true);
    assert.match(s.find("raw-response-dialog").textContent, /rejected and was NOT applied/);
    assert.equal(s.find("raw-response-text").textContent, raw);
    await s.find("copy-raw-response").emit("click");
    assert.equal(copied, raw);
    assert.match(s.find("raw-response-dialog").textContent, /Raw response copied/);
    s.api.buildInitialRange = async () => { throw new Error("The next Story operation failed."); };
    await s.planner.buildDraft();
    assert.equal(s.find("view-raw-response").hidden, true);
    assert.equal(s.find("raw-response-text").textContent, "");
  } finally {
    if (originalNavigator === undefined) delete globalThis.navigator;
    else Object.defineProperty(globalThis, "navigator", { configurable: true, value: originalNavigator });
  }
});

test("Story API attaches only explicitly returned Story raw-response metadata to the error", async () => {
  const original = globalThis.fetch;
  const raw = "```json\n{broken\n```";
  globalThis.fetch = async () => ({ ok: false, status: 502, json: async () => ({
    detail: { message: "Story Planner returned malformed JSON at line 2, column 2.", category: "story_json", rawResponse: raw },
  }) });
  try {
    await assert.rejects(realApi.buildInitialRange("project", {}), error => {
      assert.match(error.message, /line 2, column 2/);
      assert.equal(error.rawResponse, raw);
      return true;
    });
  } finally { globalThis.fetch = original; }
});

test("Story API presents string and structured conflict details as readable errors", async () => {
  const original = globalThis.fetch;
  const payloads = [
    { detail: "The project changed while the Story Planner was running." },
    { detail: { category: "story_conflict", message: "The selected generation changed." } },
  ];
  globalThis.fetch = async () => ({
    ok: false, status: 409, json: async () => payloads.shift(),
  });
  try {
    await assert.rejects(realApi.buildStoryPlan("project", {}), error => {
      assert.equal(error.message, "The project changed while the Story Planner was running.");
      return true;
    });
    await assert.rejects(realApi.buildStoryPlan("project", {}), error => {
      assert.equal(error.message, "The selected generation changed.");
      return true;
    });
  } finally { globalThis.fetch = original; }
});

const visible = element => Boolean(element) && !element.hidden && (!element.parentNode || visible(element.parentNode));

test("Revision field owns visibility across ordinary, bounded and ordinary drafts", async () => {
  const s = setup();
  const instruction = s.find("instruction");
  const revisionControls = [instruction.parentNode, instruction, s.find("scope-heading"), s.find("scope-scene_plan"), s.find("revise")];
  const checkEditable = async () => {
    for (const control of revisionControls) assert.equal(visible(control), true);
    assert.match(instruction.parentNode.textContent, /Revision instruction/);
    await s.select(1); await s.check(s.find("scope-scene_plan"));
    await s.type(instruction, "Develop the action");
    assert.equal(instruction.value, "Develop the action");
    assert.equal(Boolean(instruction.disabled), false);
    assert.equal(Boolean(s.find("revise").disabled), false);
    for (const id of ["apply", "discard"]) assert.equal(visible(s.find(id)), true);
  };
  s.ordinary(); await checkEditable();
  await s.build();
  for (const control of revisionControls) assert.equal(visible(control), false);
  assert.equal(Boolean(instruction.hidden), false);
  s.ordinary(); await checkEditable();
});

test("saved Scene Plan follows all operation sections and reloads the selected Generation", async () => {
  const s = setup();
  const input = s.find("applied-scene-plan"), label = input.parentNode;
  assert.match(label.textContent, /^Scene Plan — Current Generation/);
  assert.equal(label.parentNode.id, "story-current-scene-plan");
  assert.equal(label.parentNode.parentNode, s.root);
  assert.equal(input.closest("details"), undefined);
  for (const id of ["build-section", "memory", "creative-direction", "next-section", "insert-section", "replan-section"]) {
    assert.ok(s.root.children.indexOf(s.find(id)) < s.root.children.indexOf(label.parentNode));
  }
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  assert.ok(html.indexOf('id="story-planner"') < html.indexOf('Planned Continuity'));
  for (const [id, text] of [["generation-a", "Saved first scene"], ["generation-b", "Saved second scene"]]) {
    s.context.activeGenerationId = id;
    s.planner.restoreScenePlan(text);
    assert.equal(input.value, text);
  }
  await s.type(input, "Edited second scene");
  assert.equal(s.context.scenePlan, "Edited second scene");
  assert.equal(s.context.dirty, true);
  assert.equal(s.calls.length, 0);
});

test("cancelled Revision keeps the editable draft and saved authority despite a late result", async () => {
  const s = setup(); s.ordinary();
  await s.select(1); await s.check(s.find("scope-scene_plan"));
  await s.type(s.find("instruction"), "Develop the action");
  const before = structuredClone(s.context), scene = s.input(1, "scenePlan").value;
  let finish, signal;
  s.api.reviseStoryPlan = (_id, _body, abortSignal) => {
    signal = abortSignal; return new Promise(resolve => { finish = resolve; });
  };
  const pending = s.planner.reviseDraft();
  await s.find("cancel").emit("click");
  assert.equal(signal.aborted, true);
  assert.equal(s.planner.hasPendingRequest, false);
  assert.match(s.find("status").textContent, /cancelled/);
  const late = proposal(); late.items[1].scenePlan = "Abandoned result";
  finish(late); await pending;
  assert.equal(s.input(1, "scenePlan").value, scene);
  assert.deepEqual(s.context, before);
  assert.deepEqual(s.adopted, []);
});

test("mounted authoring buttons belong to source controls with unique canonical nodes", async () => {
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  const page = mountHtml(html, doc);
  const storyButton = page.querySelector("#generate-story");
  let generated = 0;
  storyButton.addEventListener("click", () => { generated++; });
  const s = setup({ mountedRoot: page.querySelector("#story-planner"), generateStoryButton: storyButton });
  const main = page.querySelector("#generate");
  assert.equal(page.querySelectorAll("#generate").length, 1);
  assert.equal(page.querySelectorAll("#generate-story").length, 1);
  assert.equal(main.closest(".author-panel"), page.querySelector(".author-panel"));
  assert.equal(storyButton.closest("#story-current-scene-plan"), s.find("applied-scene-plan").closest("#story-current-scene-plan"));
  const order = domOrder(page);
  const before = (a, b) => assert.ok(order.indexOf(a) < order.indexOf(b));
  before(page.querySelector("#h3-prompt-detail-controls"), main);
  before(main, page.querySelector("#exact-dialogue-section"));
  before(s.find("applied-scene-plan"), storyButton);
  before(storyButton, page.querySelector("#continuity-section"));
  assert.equal(page.querySelector(".generate-bar").querySelectorAll("#generate, #generate-story").length, 0);
  await storyButton.emit("click"); assert.equal(generated, 1);
  assert.equal(storyButton.events.click.length, 1);
  const app = await readFile(new URL("../../web/js/app.js", import.meta.url), "utf8");
  assert.equal((app.match(/#generate-story"\)\.addEventListener\("click", \(\) => generate\("story"\)\)/g) || []).length, 1);
  assert.match(app, /state\.activeProjectType !== "filesystem" \|\| state\.dirty \|\| !state\.scenePlan\.trim\(\)/);
  assert.match(app, /savedScenePlan: state\.savedScenePlan/);
});

test("blank and unsaved Scene Plan feedback stays contextual without granting editor authority", () => {
  assert.match(storyGenerationFeedback({ visibleScenePlan: "  ", savedScenePlan: "" }), /no Scene Plan yet.*Build Initial Range.*save the Project/);
  for (const [visibleScenePlan, savedScenePlan] of [["Unsaved plan", ""], ["Changed plan", "Saved plan"], ["", "Saved plan"]])
    assert.equal(storyGenerationFeedback({ visibleScenePlan, savedScenePlan }), "Save the current Scene Plan before Generate from Story.");
});

test("Plan Next mounts the single shared local progress beside its initiating action", async () => {
  let tick, timers = 0, now = 0;
  const activity = createProviderActivity({ now: () => now, setTimer: callback => { tick = callback; timers++; return 1; }, clearTimer: () => {} });
  const s = nextSetup();
  const root = doc.createElement("div");
  let finish, signal;
  const planner = createStoryPlanner({ root, activity, api: { planStoryNext: (_id, _body, abortSignal) => { signal = abortSignal; return new Promise(resolve => { finish = resolve; }); } },
    getContext: () => s.context, getSettings: () => ({ modelId: "fake" }), adoptProject: () => {} });
  const pending = planner.planNextDraft();
  const running = root.querySelector("#story-running");
  assert.equal(running.hidden, false);
  assert.equal(running.parentNode, root.querySelector("#story-next-section"));
  assert.equal(root.querySelector("#story-build-section").querySelector("#story-running"), null);
  assert.equal(root.querySelectorAll("#story-running").length, 1);
  now = 61000; tick(); assert.match(running.textContent, /Plan Next · 01:01/);
  assert.equal(timers, 1);
  await root.querySelector("#story-cancel").emit("click");
  assert.equal(signal.aborted, true); assert.equal(running.hidden, true);
  finish(nextProposal()); await pending;
});

test("moved Story button runs canonical application feedback and generation without a provider", async () => {
  const app = await readFile(new URL("../../web/js/app.js", import.meta.url), "utf8");
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  for (const [visibleScenePlan, savedScenePlan, dirty, expected] of [
    ["", "", false, /no Scene Plan yet/],
    ["Unsaved scene", "", true, /Save the current Scene Plan/],
    ["Edited scene", "Saved scene", true, /Save the current Scene Plan/],
    ["Saved scene", "Saved scene", false, null],
  ]) {
    const page = mountHtml(html, doc);
    const button = page.querySelector("#generate-story");
    const s = setup({ mountedRoot: page.querySelector("#story-planner"), generateStoryButton: button });
    s.planner.restoreScenePlan(visibleScenePlan);
    const activity = createProviderActivity({ setTimer: () => 1, clearTimer: () => {} });
    const state = { activeProjectType: "filesystem", dirty, scenePlan: visibleScenePlan, savedScenePlan };
    const calls = [], toasts = [];
    const noop = () => {};
    const scope = {
      state, AbortController, Date, storyGenerationFeedback,
      $: selector => { const node = page.querySelector(selector) || {}; node.classList = { add: noop }; return node; },
      providerActivity: activity, providerContextEpoch: 0, providerContextCurrent: () => true,
      providerAttempt: (id, label, _status, cancel) => activity.begin({ id, label, owner: "app", cancel }),
      generationClickIntent: () => "start", markRevisionStale: noop, persistSettings: noop,
      ensureDialogueResolution: async () => {}, snapshotGenerationRequest: value => value,
      syncReferenceAuthority: async () => {},
      buildGenerateRequest: path => ({ workspace: { variations: 1 }, authoringPath: path }),
      api: { streamGenerate: async request => { calls.push(request); } },
      toast: message => toasts.push(message), updateEjectButton: noop, renderWarnings: noop,
      setGenerationStatus: noop, setGenerationControls: noop, saveCompletedGenerationHistory: async () => {},
      workflowPreparation: null, renderedTakes: null,
    };
    const source = app.slice(app.indexOf('async function generate(authoringPath'), app.indexOf('function renderWarnings('));
    const listener = app.match(/  \$\("#generate-story"\)\.addEventListener\("click", \(\) => generate\("story"\)\);/)[0];
    runInNewContext(source + "\n" + listener, scope);
    await button.emit("click");
    assert.equal(button.events.click.length, 1);
    if (expected) {
      assert.match(page.querySelector("#story-generation-feedback").textContent, expected);
      assert.match(toasts[0], expected); assert.equal(calls.length, 0);
    } else {
      assert.equal(calls.length, 1); assert.equal(calls[0].authoringPath, "story");
      assert.equal(toasts.length, 0);
    }
    assert.equal(activity.active(), null);
  }
});
