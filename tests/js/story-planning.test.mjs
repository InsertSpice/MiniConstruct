import assert from "node:assert/strict";
import test from "node:test";
import { createStoryPlanner } from "../../web/js/story-planning.js";
import { shouldMarkWorkspaceDirty } from "../../web/js/sequence-workflow.js";
import { api as realApi } from "../../web/js/api.js";
import { persistFilesystemProject } from "../../web/js/project-workflow.js";

// Small DOM test double: exercise mounted controls/events, never inspect JS source.
class Element {
  constructor(tag, doc) {
    this.tagName = tag; this.ownerDocument = doc; this.children = []; this.dataset = {};
    this.events = {}; this.attributes = {}; this.value = ""; this.checked = false;
  }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || "") + this.children.map(child => child.textContent).join(""); }
  set innerHTML(_) { throw new Error("Unsafe HTML rendering"); }
  setAttribute(key, value) { this.attributes[key] = value; }
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
  assert.equal(s.calls[0].body.idea, exact);
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

test("Story idea survives failed operations, canceled navigation and successful Apply", async () => {
  const s = setup({ canonicalIdea: true });
  s.planner.restoreStoryIdea("Saved exact idea");
  s.api.buildStoryPlan = async () => { throw new Error("Build failed"); };
  await s.planner.buildDraft();
  assert.equal(s.planner.storyIdea, "Saved exact idea");
  s.api.buildStoryPlan = async () => proposal();
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
  s.api.applyStoryPlan = async () => { throw new Error("Apply failed"); };
  await s.planner.applyDraft();
  assert.equal(s.planner.storyIdea, "Saved exact idea");
  s.api.applyStoryPlan = async () => ({ id: "project", storyIdea: "Saved exact idea" });
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
  s.context.activeGenerationId = "another";
  s.context.generations[0].referenceAssets = [{ id: "dog", filename: "dog.png", role: "subject_identity" }];
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
      creativeRequest: `Action ${index}`, incomingState: `Start ${index}`, intendedOutgoingState: `End ${index}`,
      relationshipToPrevious: index === 0 ? null : "independent", cameraIntent: "Wide", referenceRecommendations: [] })),
    consistencyWarnings: [],
  };
}

function setup({ response = proposal(), canonicalIdea = false } = {}) {
  const parent = doc.createElement("aside"); parent.className = "left-rail";
  const sequence = doc.createElement("section"); sequence.id = "sequence-panel"; parent.append(sequence);
  const root = doc.createElement("div"); sequence.append(root);
  const context = { saved: true, dirty: false, projectId: "project", generations: [{ generationId: "generation-a" }] };
  const calls = [], prompts = [], adopted = [], applyBusy = [];
  let approved = true;
  const api = {
    buildStoryPlan: async (id, body) => { calls.push({ action: "build", id, body }); return structuredClone(response); },
    reviseStoryPlan: async (id, body) => { calls.push({ action: "revise", id, body }); return structuredClone(body.draft); },
    applyStoryPlan: async (id, body) => { calls.push({ action: "apply", id, body }); return { id, sequence: { generations: body.draft.items } }; },
  };
  const planner = createStoryPlanner({ root, api, getContext: () => context, getSettings: () => ({ modelId: "fake", timeoutSeconds: 120 }),
    onIdeaChange: () => { if (canonicalIdea) context.dirty = true; },
    adoptProject: project => { adopted.push(project); context.dirty = false; },
    confirmDiscard: text => { prompts.push(text); return approved; }, onApplyBusy: value => applyBusy.push(value),
  });
  const find = id => root.querySelector(`#story-${id}`);
  const cards = () => root.querySelectorAll("fieldset");
  const input = (index, field) => cards()[index].querySelectorAll("textarea, select").find(el => el.dataset.field === field);
  const type = async (el, value) => { el.value = value; await el.emit("input"); };
  const check = async (el, checked = true) => { el.checked = checked; await el.emit("change"); };
  const select = index => check(cards()[index].querySelector("input"));
  const build = async () => { await type(find("idea"), "A cyclist rescues a dog."); await planner.buildDraft(); };
  return { root, context, calls, prompts, adopted, applyBusy, api, planner, find, cards, input, type, check, select, build,
    approve: value => { approved = value; } };
}

test("mounted Story Planner gates unsaved/dirty/oversize projects and validates total count", async () => {
  const s = setup();
  await s.type(s.find("idea"), "Idea");
  for (const patch of [{ saved: false }, { saved: true, dirty: true }]) {
    Object.assign(s.context, patch); s.planner.refresh();
    assert.equal(s.find("build").disabled, true);
    await s.planner.buildDraft();
  }
  assert.equal(s.calls.length, 0);
  s.context.dirty = false; s.planner.refresh();
  assert.equal(s.find("build").disabled, false);
  for (const value of ["1", "13", "2.5", ""]) {
    await s.type(s.find("count"), value);
    assert.equal(s.find("build").disabled, true);
  }
  s.context.generations = Array.from({ length: 5 }, () => ({}));
  await s.type(s.find("count"), "4"); assert.equal(s.find("build").disabled, true);
  await s.type(s.find("count"), "5"); assert.equal(s.find("build").disabled, false);
});

test("Build serializes idea, TOTAL count, settings; adopts only the ephemeral draft", async () => {
  const s = setup(); await s.build();
  assert.deepEqual(s.calls[0], { action: "build", id: "project", body: {
    idea: "A cyclist rescues a dog.", desiredGenerationCount: 4, llm: { modelId: "fake", timeoutSeconds: 600 },
  } });
  assert.equal(s.cards().length, 4);
  assert.equal(s.adopted.length, 0);
  assert.equal(s.context.generations.length, 1);
  assert.equal(s.context.dirty, false);
  assert.equal(s.cards()[0].querySelector("legend").textContent, "Generation 1 · Existing Generation");
  assert.equal(s.cards()[3].querySelector("legend").textContent, "Generation 4 · New Generation");
  assert.equal(s.input(0, "relationshipToPrevious").disabled, true);
  assert.equal(s.input(1, "relationshipToPrevious").querySelectorAll("option").length, 4);
  const scopeLabel = s.find("scope-camera_intent").parent;
  assert.equal(scopeLabel.children[0], s.find("scope-camera_intent"));
  assert.equal(scopeLabel.className, "story-scope-option");
});

test("incomplete Build fields remain editable and show a local planner warning", async () => {
  const response = proposal();
  response.items[0].creativeRequest = "";
  response.consistencyWarnings = [{ planItemId: "item-0", relatedPlanItemId: "item-0", direction: "downstream",
    message: "Build left these planning fields incomplete: Creative Request. Edit the draft before Apply." }];
  const s = setup({ response }); await s.build();
  assert.equal(s.find("warnings").textContent, `Generation 1: ${response.consistencyWarnings[0].message}`);
  assert.equal(s.input(0, "creativeRequest").disabled, false);
  await s.type(s.input(0, "creativeRequest"), "The cyclist spots a dog.");
  await s.planner.applyDraft();
  assert.equal(s.calls.at(-1).body.draft.items[0].creativeRequest, "The cyclist spots a dog.");
});

test("Story timeout uses the existing request setting for Build and Revision", async () => {
  const s = setup();
  assert.equal(s.find("timeout").value, "600");
  await s.type(s.find("idea"), "Idea");
  for (const value of ["0", "601", "", "invalid"]) {
    await s.type(s.find("timeout"), value);
    assert.equal(s.find("build").disabled, true);
  }
  await s.type(s.find("timeout"), "360");
  await s.planner.buildDraft();
  assert.equal(s.calls[0].body.llm.timeoutSeconds, 360);
  await s.select(1); await s.check(s.find("scope-camera_intent"));
  await s.type(s.find("instruction"), "A wider shot");
  await s.planner.reviseDraft();
  assert.equal(s.calls.at(-1).body.llm.timeoutSeconds, 360);
});

test("manual edits retain textarea identity, caret and focus; revision uses the latest full draft", async () => {
  const s = setup(); await s.build();
  const textarea = s.input(1, "creativeRequest");
  textarea.selectionStart = 7; textarea.selectionEnd = 7; doc.activeElement = textarea;
  await s.type(textarea, "Manual text\n  🙂");
  await s.type(s.find("context"), "Manual world");
  assert.equal(s.input(1, "creativeRequest"), textarea);
  assert.equal(textarea.selectionStart, 7);
  assert.equal(doc.activeElement, textarea);
  assert.equal(shouldMarkWorkspaceDirty(textarea), false);
  assert.equal(shouldMarkWorkspaceDirty(s.find("context")), false);
  assert.equal(s.context.dirty, false);
  await s.select(1); await s.select(2);
  assert.equal(s.root.querySelectorAll("input").filter(input => input.id?.startsWith("story-scope-")).length, 5);
  await s.check(s.find("scope-camera_intent"));
  await s.type(s.find("instruction"), "More dynamic cameras only");
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
  assert.equal(request.draft.items[1].creativeRequest, "Manual text\n  🙂");
  assert.equal(request.draft.sequenceContext, "Manual world");
  assert.equal(s.input(1, "creativeRequest").value, "Manual text\n  🙂");
  assert.equal(s.input(1, "cameraIntent").value, "Track");
  assert.equal(s.input(0, "cameraIntent").value, "Wide");
  assert.equal(s.context.dirty, false);
});

test("revision failure preserves controls, manual values, selection and draft", async () => {
  const s = setup(); await s.build();
  await s.select(1); await s.check(s.find("scope-camera_intent"));
  await s.type(s.find("instruction"), "Change camera");
  const textarea = s.input(2, "creativeRequest");
  await s.type(textarea, "Do not touch");
  s.api.reviseStoryPlan = async () => { throw new Error("Model changed draft identity"); };
  await s.planner.reviseDraft();
  assert.equal(s.input(2, "creativeRequest"), textarea);
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
  const s = setup({ response }); await s.build();
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
  await s.build();
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
  const originalApply = s.api.applyStoryPlan;
  s.api.applyStoryPlan = async () => { throw new Error("Underlying Project changed; rebuild draft"); };
  await s.planner.applyDraft();
  assert.equal(s.planner.hasDraft, true); assert.equal(s.adopted.length, 0);
  assert.match(s.find("action-status").textContent, /rebuild/);
  s.api.applyStoryPlan = originalApply;
  await s.planner.applyDraft();
  assert.equal(s.adopted.length, 1); assert.equal(s.adopted[0].sequence.generations.length, 4);
  assert.equal(s.planner.hasDraft, false); assert.equal(s.cards().length, 0);
  assert.equal(s.context.dirty, false);
  assert.deepEqual(s.applyBusy, [true, false, true, false]);
  assert.deepEqual(s.calls.map(c => c.action), ["build", "apply"]);
  assert.equal(s.find("idea").value, "A cyclist rescues a dog.");
});

test("dirty canonical edits disable Apply/Revise without discarding either state", async () => {
  const s = setup(); await s.build();
  s.context.dirty = true; s.planner.refresh();
  assert.equal(s.find("apply").disabled, true); assert.equal(s.find("revise").disabled, true);
  await s.planner.applyDraft(); assert.equal(s.adopted.length, 0);
  assert.equal(s.planner.hasDraft, true);
  await s.find("discard").emit("click");
  assert.equal(s.planner.hasDraft, false); assert.equal(s.context.dirty, true);
  assert.equal(s.context.generations.length, 1);
});

test("action failures stay beside the action and successful revision replaces stale error text", async () => {
  const s = setup(); await s.build();
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
  s.api.buildStoryPlan = () => new Promise(resolve => { finish = resolve; });
  const pending = s.build();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(s.planner.hasDraft, true);
  assert.equal(s.planner.confirmContextChange({ message: "Switch Generation?" }), true);
  finish(proposal()); await pending;
  assert.equal(s.planner.hasDraft, false); assert.equal(s.cards().length, 0);
});

test("Project reset invalidates pending Build even before the Project ID changes", async () => {
  const s = setup(); let finish;
  s.api.buildStoryPlan = () => new Promise(resolve => { finish = resolve; });
  const pending = s.build();
  await new Promise(resolve => setImmediate(resolve));
  s.planner.resetProjectContext();
  assert.equal(s.find("idea").value, "");
  finish(proposal()); await pending;
  assert.equal(s.planner.hasDraft, false);
  assert.equal(s.cards().length, 0);
  assert.equal(s.find("status").textContent, "");
});

test("Apply in flight prevents context change until its outcome is known", async () => {
  const s = setup(); await s.build(); let finish;
  s.api.applyStoryPlan = () => new Promise(resolve => { finish = resolve; });
  const pending = s.planner.applyDraft();
  assert.equal(s.planner.confirmContextChange(), false);
  assert.equal(s.prompts.length, 0);
  finish({ id: "project" }); await pending;
  assert.equal(s.adopted.length, 1);
});

test("Build failure stays visible and a previous draft survives", async () => {
  const s = setup(); await s.build();
  s.api.buildStoryPlan = async () => { throw new Error("Invalid model structure"); };
  await s.planner.buildDraft();
  assert.equal(s.cards().length, 4); assert.match(s.find("status").textContent, /Invalid model structure/);
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
