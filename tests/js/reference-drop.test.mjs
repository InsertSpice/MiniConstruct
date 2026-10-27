import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";
import { availableSubjects, createSubject, defaultSubjectIdentity, materializeSubject } from "../../web/js/subject-identity.js";
import { Element } from "./support/dom.mjs";

const app = await readFile(new URL("../../web/js/app.js", import.meta.url), "utf8");
const functionSource = name => app.match(new RegExp(`(?:async )?function ${name}\\([^\\n]*\\) \\{[\\s\\S]*?\\n\\}`))?.[0];
const production = ["defaultImageRole", "createAssetFromFile", "ingestAssetFiles"].map(functionSource).join("\n");

function ingestionContext({ mode = "Ref2VA", subjects = [], sequence = null, assets = [], processImage = async () => ({ data_url: "data:image/jpeg;base64,AA==", width: 1, height: 1 }) } = {}) {
  const state = { mode, currentProjectId: "project-a", activeGenerationId: "generation-a", projectSequence: sequence,
    subjects: structuredClone(subjects), assets: structuredClone(assets), nextSubjectNumber: 1,
    pendingAssetKind: null, pendingAssetSubjectId: null, reattachId: null };
  const context = {
    state, roleCatalog: { image: [["general_visual", "General visual"], ["first_frame_anchor", "First frame"], ["last_frame_anchor", "Last frame"], ["subject_identity", "Subject identity"]], video: [["video", "Video"]], audio: [["audio", "Audio"]] },
    uid: (() => { let n = 0; return () => `asset-${++n}`; })(), defaultSubjectIdentity,
    processImage, readMediaReference: async (file, kind) => ({ filename: file.name, kind, mimeType: file.type, mediaPayload: { name: file.name } }),
    readDuration: async () => null, classifyAssetFile: file => file.type.startsWith("image/") ? "image" : file.type.startsWith("video/") ? "video" : file.type.startsWith("audio/") ? "audio" : null,
    replacePicture: () => ({}), availableSubjects, materializeSubject,
    createSubject, normalizeSubjectIdentity: value => value || { subjectId: null },
    isSubjectIdentityAsset: asset => asset.kind === "image" && asset.role === "subject_identity",
    normalizeSubjectsInState() {}, renderAssets() {}, changeReferenceAssets: async change => change(),
    toast: message => context.toasts.push(message), toasts: [],
  };
  context.assignNewSubject = asset => {
    const created = createSubject({ subjects: state.subjects, nextSubjectNumber: state.nextSubjectNumber }, state.projectSequence);
    state.subjects.push(created.subject); state.nextSubjectNumber = created.nextSubjectNumber;
    asset.subjectIdentity = { ...asset.subjectIdentity, subjectId: created.subject.id };
  };
  return context;
}

const file = (name, type = "image/png") => ({ name, type, size: 1 });

test("actual Ref2VA ingestion gives general drops no implicit Subject, even with one existing", async () => {
  for (const subjects of [[], [{ id: "subject-z", number: 7 }]]) {
    const context = ingestionContext({ subjects });
    await runInNewContext(`${production}; (async () => { await ingestAssetFiles([file], null, null, null, ""); })()`, { ...context, file: file("general.png") });
    assert.equal(context.state.assets[0].role, "general_visual");
    assert.deepEqual(context.state.subjects, subjects);
  }
});

test("actual ingestion attaches one or many dropped images to the exact existing Subject", async () => {
  const subjects = [{ id: "subject-a", number: 1 }, { id: "stable-uuid-for-subject-b", number: 2 }];
  const context = ingestionContext({ subjects });
  const files = [file("front.png"), file("side.png"), file("sheet.png")];
  await runInNewContext(`${production}; (async () => { await ingestAssetFiles(files, null, null, null, "stable-uuid-for-subject-b"); })()`, { ...context, files });
  assert.equal(context.state.assets.length, 3);
  assert.deepEqual(context.state.assets.map(asset => asset.role), Array(3).fill("subject_identity"));
  assert.deepEqual(context.state.assets.map(asset => asset.subjectIdentity.subjectId), Array(3).fill("stable-uuid-for-subject-b"));
  assert.deepEqual(context.state.subjects, subjects);
});

test("a Subject removed while image processing is pending rejects the drop without reassignment", async () => {
  let finish;
  const context = ingestionContext({ subjects: [{ id: "subject-a", number: 1 }], processImage: () => new Promise(resolve => { finish = resolve; }) });
  const pending = runInNewContext(`${production}; (async () => { await ingestAssetFiles([file], null, null, null, "subject-a"); })()`, { ...context, file: file("pending.png") });
  context.state.subjects = [];
  finish({ data_url: "data:image/jpeg;base64,AA==", width: 1, height: 1 });
  await pending;
  assert.deepEqual(context.state.assets, []);
  assert.deepEqual(context.state.subjects, []);
  assert.match(context.toasts[0], /Unsupported or incompatible/);
});

test("Ref2VA Video and Audio retain their media kinds when dropped on a Subject group", async () => {
  const context = ingestionContext({ subjects: [{ id: "subject-a", number: 1 }] });
  const files = [{ name: "clip.mp4", type: "video/mp4", size: 1 }, { name: "voice.wav", type: "audio/wav", size: 1 }, file("face.png")];
  await runInNewContext(`${production}; (async () => { await ingestAssetFiles(files, null, null, null, "subject-a"); })()`, { ...context, files });
  assert.deepEqual(context.state.assets.map(asset => asset.kind), ["video", "audio", "image"]);
  assert.deepEqual(context.state.assets.map(asset => asset.role), ["video", "audio", "subject_identity"]);
  assert.equal(context.state.assets[2].subjectIdentity.subjectId, "subject-a");
  assert.deepEqual(context.state.subjects, [{ id: "subject-a", number: 1 }]);
});

test("project or Generation changes during asynchronous processing cannot receive the drop", async () => {
  for (const change of [state => { state.currentProjectId = "project-b"; }, state => { state.activeGenerationId = "generation-b"; }]) {
    let finish;
    const context = ingestionContext({ subjects: [{ id: "subject-a", number: 1 }], processImage: () => new Promise(resolve => { finish = resolve; }) });
    const pending = runInNewContext(`${production}; (async () => { await ingestAssetFiles([file], null, null, null, "subject-a"); })()`, { ...context, file: file("pending.png") });
    change(context.state);
    finish({ data_url: "data:image/jpeg;base64,AA==", width: 1, height: 1 });
    await pending;
    assert.deepEqual(context.state.assets, []);
    assert.deepEqual(context.state.subjects, [{ id: "subject-a", number: 1 }]);
  }
});

test("explicit New Subject picker still creates a Subject; non-Ref2VA drop roles stay mode-specific", async () => {
  const explicit = ingestionContext();
  await runInNewContext(`${production}; (async () => { await ingestAssetFiles([file], "image"); })()`, { ...explicit, file: file("new.png") });
  assert.equal(explicit.state.assets[0].role, "subject_identity");
  assert.equal(explicit.state.subjects.length, 1);
  for (const [mode, expected] of [["I2VA", "first_frame_anchor"], ["FL2VA", "first_frame_anchor"], ["L2VA", "last_frame_anchor"]]) {
    const context = ingestionContext({ mode });
    await runInNewContext(`${production}; (async () => { await ingestAssetFiles([file], null, null, null, ""); })()`, { ...context, file: file(`${mode}.png`) });
    assert.equal(context.state.assets[0].role, expected);
    assert.deepEqual(context.state.subjects, []);
  }
});

function dropHarness(mode = "Ref2VA") {
  const doc = { events: {}, addEventListener(name, fn) { this.events[name] = fn; } };
  const panel = { events: {}, classes: new Set(), classList: { add(v) { panel.classes.add(v); }, remove(v) { panel.classes.delete(v); } },
    addEventListener(name, fn) { this.events[name] = fn; }, contains(node) { return node === this || node?.insidePanel === true; } };
  const hint = { textContent: "" }, calls = [], state = { mode, subjects: [{ id: "uuid-2", number: 2 }] };
  const context = { state, $: selector => selector === "#references-panel" ? panel : hint,
    hasFiles: event => [...(event.dataTransfer?.types || [])].includes("Files"),
    ingestAssetFiles: (...args) => { calls.push(args); return Promise.resolve(); }, toast() {}, document: doc };
  runInNewContext(`${functionSource("setupAssetDropTarget")}`, context);
  context.setupAssetDropTarget();
  return { context, doc, panel, hint, calls, state };
}

function target(id, insidePanel = true) {
  const classes = new Set();
  return { insidePanel, dataset: { subjectId: id }, classes,
    classList: { add(name) { classes.add(name); }, remove(name) { classes.delete(name); } },
    closest(selector) { return selector.includes("reference-subject-group") ? this : null; } };
}

test("actual drop handler resolves summary and expanded content groups by canonical DOM UUID", () => {
  for (const dropZone of ["summary", "contents"]) {
    const h = dropHarness();
    const group = target("uuid-2");
    const dropTarget = dropZone === "summary"
      ? group
      : { insidePanel: true, closest: selector => selector.includes("reference-subject-group") ? group : null };
    const event = { target: dropTarget, dataTransfer: { types: ["Files"], files: [file("view.png")] }, preventDefault() {}, stopPropagation() {} };
    h.panel.events.drop(event);
    assert.equal(h.calls[0][4], "uuid-2", `${dropZone} drop carries canonical Subject UUID`);
    assert.equal(h.panel.classes.has("drop-active"), false);
    assert.equal(group.classes.has("drop-target"), false);
  }
});

test("actual drop handler sends general drops to the general default and gives transient feedback", () => {
  const h = dropHarness();
  const general = { insidePanel: true, closest: () => null };
  h.panel.events.dragenter({ target: general, dataTransfer: { types: ["Files"] }, preventDefault() {} });
  assert.equal(h.panel.classes.has("drop-active"), true);
  h.panel.events.dragover({ target: general, dataTransfer: { types: ["Files"], dropEffect: "none" }, preventDefault() {} });
  assert.match(h.hint.textContent, /Images become General Visual References/);
  assert.match(h.hint.textContent, /video\/audio stay shared/);
  h.panel.events.drop({ target: general, dataTransfer: { types: ["Files"], files: [file("general.png")] }, preventDefault() {}, stopPropagation() {} });
  assert.equal(h.calls[0][4], "");
  assert.equal(h.panel.classes.has("drop-active"), false);
});

test("nested drag transitions keep feedback active and switch from general area to the existing Subject", () => {
  const h = dropHarness();
  const general = { insidePanel: true, closest: () => null };
  const group = target("uuid-2");
  h.panel.events.dragenter({ target: general, dataTransfer: { types: ["Files"] }, preventDefault() {} });
  h.panel.events.dragenter({ target: group, dataTransfer: { types: ["Files"] }, preventDefault() {} });
  h.panel.events.dragleave({ target: general, relatedTarget: group, dataTransfer: { types: ["Files"] } });
  assert.equal(h.panel.classes.has("drop-active"), true);
  assert.equal(group.classes.has("drop-target"), true);
  assert.match(h.hint.textContent, /attach to existing Subject 2/);
  assert.match(h.hint.textContent, /video\/audio stay shared/);
  h.panel.events.dragleave({ target: group, relatedTarget: { insidePanel: false }, dataTransfer: { types: ["Files"] } });
  assert.equal(group.classes.has("drop-target"), false);
  assert.equal(h.panel.classes.has("drop-active"), false);
});

test("drag cancellation, outside drop, rerender and navigation clear transient feedback", () => {
  for (const cleanup of [
    h => h.doc.events.dragend(),
    h => h.doc.events.drop({ target: { insidePanel: false }, dataTransfer: { types: ["Files"] }, preventDefault() {} }),
    h => h.state.clearAssetDrop(), // renderAssets invokes this before replacing dynamic Subject groups.
    h => h.state.clearAssetDrop(), // setMode invokes renderAssets during navigation.
  ]) {
    const h = dropHarness();
    const group = target("uuid-2");
    h.panel.events.dragenter({ target: group, dataTransfer: { types: ["Files"] }, preventDefault() {} });
    assert.equal(group.classes.has("drop-target"), true);
    cleanup(h);
    assert.equal(group.classes.has("drop-target"), false);
    assert.equal(h.panel.classes.has("drop-active"), false);
  }
});
