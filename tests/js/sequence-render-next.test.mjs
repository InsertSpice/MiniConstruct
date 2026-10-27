import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { Element } from "./support/dom.mjs";
import { createSequenceRenderAction } from "../../web/js/sequence-render-next.js";

function mount(response) {
  const doc = { createElement: tag => new Element(tag, doc) };
  const button = new Element("button", doc), status = new Element("div", doc);
  const calls = [];
  const api = { sequenceRenderReadiness: async projectId => {
    calls.push(["readiness", projectId]); return response;
  }, sendExecution: () => { throw new Error("Send must remain separate"); } };
  createSequenceRenderAction({ button, status, api, getProjectId: () => "project-1",
    openAndPrepare: async next => { calls.push(["open-and-prepare", next.generationId, next.profileId]); } });
  return { button, status, calls };
}

test("generated-state reset clears readiness and ignores late same-Project candidates", async () => {
  const doc = { createElement: tag => new Element(tag, doc) };
  const button = new Element("button", doc), status = new Element("div", doc);
  let finish; const prepared = [];
  const ui = createSequenceRenderAction({ button, status, getProjectId: () => "same-project",
    api: { sequenceRenderReadiness: () => new Promise(resolve => { finish = resolve; }) },
    openAndPrepare: next => prepared.push(next) });
  const pending = button.emit("click"); ui.reset();
  finish({ candidate: true, prepareReady: true, reason: "OLD READY", promptSummary: "OLD PROMPT" });
  await pending;
  assert.equal(status.textContent, ""); assert.deepEqual(prepared, []); assert.equal(button.disabled, false);
});

test("mounted Sequence action opens exact UUID, shows summary and never sends", async () => {
  const s = mount({ candidate: true, prepareReady: true, generationId: "stable-g3-uuid",
    generationLabel: "Generation G3", profileId: "profile-uuid", profileName: "Native H3",
    promptSummary: "The courier continues", continuitySource: "Accepted native state from Generation G2",
    rendererStatus: "none", connectionName: "Local", connectionRequired: false,
    workflowControls: [{ label: "Steps", value: 8 }], reason: "Generation G3 is ready." });
  await s.button.emit("click");
  assert.deepEqual(s.calls, [["readiness", "project-1"], ["open-and-prepare", "stable-g3-uuid", "profile-uuid"]]);
  assert.match(s.status.textContent, /Generation G3.*Native H3.*Accepted native state.*Prepare: Ready/);
  assert.match(s.status.textContent, /Steps: 8/);
  assert.match(s.status.textContent, /Send remains an explicit separate click/);
});

for (const [kind, reason] of [["active", "Generation G1 already has an active render. Watch or Cancel it."],
  ["unknown", "Generation G1 has an unresolved render attempt. Recover it first."],
  ["selection", "Generation G1 needs an explicit H3 execution selection."]]) {
  test(`mounted Sequence action explains ${kind} stop without opening another Generation`, async () => {
    const s = mount({ candidate: false, reason });
    await s.button.emit("click");
    assert.equal(s.status.textContent, reason);
    assert.deepEqual(s.calls, [["readiness", "project-1"]]);
  });
}

test("Sequence button and normal execution controls remain distinct in the mounted layout", async () => {
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  assert.match(html, /id="sequence-render-next"/);
  assert.match(html, /class="sequence-prepare-action"/);
  assert.match(html, /Prepare next ready Generation/);
  assert.match(html, /runs Prepare only\. It never generates H3 or sends to ComfyUI/);
  assert.match(html, /id="workflow-preparation-primary"/);
  assert.match(html, /id="workflow-preparation-details"/);
  const preparation = await readFile(new URL("../../web/js/workflow-preparation.js", import.meta.url), "utf8");
  assert.match(preparation, /button\("prepare", "Prepare"/);
  assert.match(preparation, /button\("send", "Send to ComfyUI"/);
});

test("mounted Sequence DOM orders Generation controls before prepare and dialogue language", async () => {
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  const root = { children: [] }, stack = [root];
  const voidTags = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"]);
  for (const token of html.matchAll(/<\/?[a-z][^>]*>/gi)) {
    const raw = token[0];
    if (raw.startsWith("</")) { stack.pop(); continue; }
    const tag = raw.match(/^<([a-z][\w-]*)/i)?.[1].toLowerCase();
    if (!tag) continue;
    const attrs = Object.fromEntries([...raw.matchAll(/([\w-]+)\s*=\s*(["'])(.*?)\2/g)].map(m => [m[1], m[3]]));
    const node = { tag, attrs, children: [] };
    stack.at(-1).children.push(node);
    if (!voidTags.has(tag) && !raw.endsWith("/>")) stack.push(node);
  }
  const ordered = [];
  const visit = node => { ordered.push(node); node.children.forEach(visit); };
  visit(root);
  const find = selector => ordered.find(node => selector.startsWith("#")
    ? node.attrs?.id === selector.slice(1)
    : (node.attrs?.class || "").split(/\s+/).includes(selector.slice(1)));
  const order = ["#generation-select", ".sequence-actions", "#sequence-render-next", "#dialogue-language"]
    .map(selector => ordered.indexOf(find(selector)));
  assert.ok(order.every(index => index >= 0), "all Sequence landmarks are mounted");
  assert.ok(order[0] < order[1] && order[1] < order[2] && order[2] < order[3],
    "selector < lifecycle action row < Prepare next ready Generation < Sequence Dialogue Language");
  assert.equal(ordered.filter(node => /^generation-(add|before|after|duplicate|earlier|later|delete)$/.test(node.attrs?.id || "")).length, 7,
    "the existing seven Generation actions remain mounted exactly once");
});
