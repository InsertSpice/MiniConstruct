import assert from "node:assert/strict";
import test from "node:test";
import { Element } from "./support/dom.mjs";
import { freshVisualHandoff, resolveVisualHandoff, renderVisualHandoff, visiblePromptWarnings } from "../../web/js/visual-handoff.js";
import { freshPlanning, planningFromProject } from "../../web/js/planning-workflow.js";

function mount(overrides = {}) {
  const doc = { createElement: tag => new Element(tag, doc) }, root = new Element("div", doc);
  const changes = [];
  const state = { policy: freshVisualHandoff(), relationship: "continue_previous_shot", dirty: false,
    summary: { status: "available", compatible: true, warnings: [] }, previewUrl: "/api/projects/p/generations/g/visual-handoff/preview",
    onChange: value => changes.push(value), ...overrides };
  renderVisualHandoff(root, state);
  return { root, state, changes };
}

test("endpoint diagnostics distinguish accepted render evidence from explicit selection", async () => {
  for (const [sourceAuthority, label] of [["accepted_render", "previous accepted render"], ["selected_take", "previous Selected Take"]]) {
    const { root } = mount({ relationship: "new_shot_preserve_continuity",
      summary: { status: "available", compatible: true, warnings: [], sourceAuthority } });
    assert.ok(root.textContent.includes(`Source: ${label}`));
    if (sourceAuthority === "accepted_render") assert.doesNotMatch(root.textContent, /Selected Take/);
    await root.querySelector("button").emit("click");
    assert.equal(root.querySelector("img").alt, "Previous rendered endpoint");
  }
});

test("new and restored planning default to Auto and preserve explicit policy", () => {
  assert.deepEqual(freshPlanning().visualHandoff, freshVisualHandoff());
  assert.deepEqual(planningFromProject({}).visualHandoff, freshVisualHandoff());
  const policy = { strategy: "endpoint_frame", tailSeconds: 1, immediateCutMilliseconds: 250 };
  assert.deepEqual(planningFromProject({ planning: { visualHandoff: policy } }).visualHandoff, policy);
});

test("all relationships resolve and explicit None overrides", () => {
  for (const [relation, expected] of Object.entries({ continue_previous_shot: "tail_reference_continuation", new_shot_preserve_continuity: "endpoint_then_immediate_cut", match_previous_ending_frame: "endpoint_frame", independent: "none" })) {
    assert.equal(resolveVisualHandoff(freshVisualHandoff(), relation), expected);
    assert.equal(resolveVisualHandoff({ strategy: "none" }, relation), "none");
  }
});

test("mounted tail control bounds authoring and creates video only after explicit preview", async () => {
  const { root, changes } = mount();
  assert.match(root.textContent, /Auto: previous 1.5 s tail continuation reference/);
  assert.match(root.textContent, /Available.*Compatible/);
  assert.equal(root.querySelectorAll("video").length, 0);
  const input = root.querySelector("input");
  input.value = "3"; await input.emit("change"); assert.equal(changes.length, 0);
  input.value = "1"; await input.emit("change"); assert.equal(changes[0].tailSeconds, 1);
  await root.querySelector("button").emit("click");
  const video = root.querySelector("video");
  assert.equal(video.controls, true); assert.equal(video.preload, "none"); assert.equal(video.autoplay, undefined);
});

test("cut controls are relevant, bounded and explicitly requested timing", async () => {
  const { root, changes } = mount({ relationship: "new_shot_preserve_continuity" });
  assert.match(root.textContent, /requested timing/);
  const input = root.querySelector("input"); assert.equal(input.value, "100");
  input.value = "501"; await input.emit("change"); assert.equal(changes.length, 0);
  input.value = "250"; await input.emit("change"); assert.equal(changes[0].immediateCutMilliseconds, 250);
  await root.querySelector("button").emit("click"); assert.ok(root.querySelector("img"));
});

test("independent, missing, incompatible and dirty states do not load previews", () => {
  for (const override of [{ relationship: "independent" }, { dirty: true },
    { summary: { status: "missing", compatible: true, warnings: ["Reattach source Take"] } },
    { summary: { status: "available", compatible: false, warnings: ["Choose Ref2VA"] } }]) {
    const { root } = mount(override);
    assert.equal(root.querySelectorAll("button, video, img").length, 0);
  }
});

test("changing policy is ordinary authoring and remount removes previous preview", async () => {
  const { root, changes, state } = mount();
  const select = root.querySelector("select"); assert.equal(select.children.length, 6);
  select.value = "none"; await select.emit("change"); assert.equal(changes[0].strategy, "none");
  await root.querySelector("button").emit("click");
  renderVisualHandoff(root, { ...state, policy: changes[0] });
  assert.equal(root.querySelectorAll("input, video, img").length, 0);
});

test("native Auto is profile-gated and has no fake media preview", () => {
  const { root } = mount({ summary: { status: "native_available", compatible: true, warnings: [],
    native: { supported: true, consume: true, sourceGenerationNumber: 1, profileName: "Native profile",
      contextFrames: 25, audioEnabled: false } } });
  assert.match(root.textContent, /Auto: Native H3 continuation/);
  assert.match(root.textContent, /Source: Generation 1.*Context: 25 frames.*disabled\/unsupported/);
  assert.equal(root.querySelectorAll("button, video, img").length, 0);
  assert.equal(root.querySelector("select").children.length, 7);
  assert.match(root.textContent, /Native continuation will use the accepted predecessor execution/);
});

test("generated warning banner excludes live handoff status in every state", () => {
  const missing = "Visual handoff omitted: No previous Selected Take; select a Take in the immediately preceding Generation.";
  const warnings = [missing, "A separate warning remains."];
  const native = { status: "native_available", native: { consume: true, requiresTailReference: false } };
  assert.deepEqual(visiblePromptWarnings(warnings, native), ["A separate warning remains."]);
  assert.deepEqual(visiblePromptWarnings(warnings, { status: "no_selected_take", native: { consume: false } }), ["A separate warning remains."]);
  assert.deepEqual(visiblePromptWarnings(warnings, { status: "native_available", native: { consume: true, requiresTailReference: true } }), ["A separate warning remains."]);
  assert.deepEqual(visiblePromptWarnings(warnings, null), ["A separate warning remains."]);
});

test("generated banner remains stable while live handoff status changes", () => {
  const doc = { createElement: tag => new Element(tag, doc) };
  const banner = new Element("div", doc);
  const missing = "Visual handoff omitted: No previous Selected Take; select a Take in the immediately preceding Generation.";
  const warnings = [missing, "A separate warning remains."];
  const render = summary => {
    const visible = visiblePromptWarnings(warnings, summary);
    banner.className = visible.length ? "" : "hidden";
    banner.textContent = visible.join(" ");
  };

  render({ status: "no_selected_take", native: { consume: false } });
  assert.doesNotMatch(banner.textContent, /No previous Selected Take/);
  render({ status: "native_available", native: { consume: true, requiresTailReference: false } });
  assert.doesNotMatch(banner.textContent, /No previous Selected Take/);
  assert.match(banner.textContent, /A separate warning remains/);
  render({ status: "native_unavailable", native: { consume: false } });
  assert.doesNotMatch(banner.textContent, /No previous Selected Take/);
  assert.deepEqual(warnings, [missing, "A separate warning remains."]);
});

test("live handoff panel still explains missing Selected Take without native state", () => {
  const { root } = mount({ summary: { status: "no_selected_take", compatible: true,
    warnings: ["Visual handoff omitted: No previous Selected Take; select a Take in the immediately preceding Generation."] } });
  assert.match(root.textContent, /No previous Selected Take/);
});

test("native plus explicitly required tail still never offers a fake latent preview", () => {
  const { root } = mount({ summary: { status: "available", compatible: true, warnings: [],
    native: { supported: true, consume: true, requiresTailReference: true, contextFrames: 25 } } });
  assert.equal(root.querySelectorAll("button, video, img").length, 0);
});

test("explicit unavailable native remains visible with actionable reason", () => {
  const { root } = mount({ policy: { ...freshVisualHandoff(), strategy: "native_h3_continuation" },
    summary: { status: "native_unavailable", compatible: false, warnings: ["Confirm predecessor execution"],
      native: { supported: false } } });
  const option = root.querySelector("select").children.find(x => x.value === "native_h3_continuation");
  assert.equal(option.disabled, true);
  assert.match(root.textContent, /Confirm predecessor execution/);
  assert.equal(root.querySelectorAll("video, img").length, 0);
});

test("native-only I2VA is deliberate, persists through planning reload, and explains unavailable state", async () => {
  const policy = { ...freshVisualHandoff(), strategy: "native_i2va_continuation" };
  assert.equal(planningFromProject({ planning: { visualHandoff: policy } }).visualHandoff.strategy, policy.strategy);
  const { root, changes } = mount({ policy, summary: { status: "native_unavailable", native: {
    supported: true, pictureFreeI2va: true, consume: false, reason: "Predecessor acceptance was revoked." } } });
  assert.match(root.textContent, /No new first-frame Picture is expected/);
  assert.match(root.textContent, /Native-only blocked.*acceptance was revoked/);
  assert.equal(root.querySelectorAll("video, img, button").length, 0);
  const select = root.querySelector("select");
  assert.equal(select.value, policy.strategy);
  select.value = "auto"; await select.emit("change");
  assert.deepEqual(changes[0], { ...policy, strategy: "auto" });
});

test("native-only I2VA identifies the explicit successor Profile and accepted source without preview", () => {
  const { root } = mount({ policy: { ...freshVisualHandoff(), strategy: "native_i2va_continuation" },
    summary: { status: "native_available", native: { supported: true, consume: true, pictureFreeI2va: true,
      profileName: "I2VA successor", sourceGenerationNumber: 2, contextFrames: 22 } } });
  assert.match(root.textContent, /Native-only ready.*I2VA successor.*accepted Generation 2/);
  assert.equal(root.querySelectorAll("video, img, button").length, 0);
  assert.equal(root.querySelector("select").value, "native_i2va_continuation");
});
