import { mediaPayloadStatus } from "../../web/js/media-assets.js";
import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";
import { createReferenceAuthority, pictureDigest, replacePicture } from "../../web/js/reference-authority.js";
import { storyGenerationFeedback } from "../../web/js/story-generation-feedback.js";
import { Element } from "./support/dom.mjs";
import * as identityHelpers from "../../web/js/subject-identity.js";
import { subjectIdentityPresentation } from "../../web/js/reference-asset-groups.js";

const dataUrl = bytes => `data:image/jpeg;base64,${Buffer.from(bytes).toString("base64")}`;
const processed = bytes => dataUrl([99, ...bytes]);
const asset = () => ({ id: "slot", kind: "image", filename: "outfit.png", mimeType: "image/jpeg", order: 0,
  role: "environment", notes: "Preserve these notes", options: { custom: true }, comparisonSubjects: ["subject-1", "subject-2"],
  subjectIdentity: { subjectId: "subject-1", focus: "outfit", view: "rear", layout: "reference_sheet" },
  image: { data_url: dataUrl([1, 2, 3]), width: 1, height: 1 } });

test("same filename replacement preserves the whole semantic slot and changes content identity", async () => {
  const old = asset();
  const image = { data_url: dataUrl([4, 5, 6]), width: 8, height: 6 };
  const replacement = replacePicture(old, { name: "outfit.png", type: "image/png" }, image);
  const { filename, mimeType, image: oldImage, ...semantics } = old;
  const { filename: newFilename, mimeType: newMime, image: newImage, ...newSemantics } = replacement;
  assert.deepEqual(newSemantics, semantics);
  assert.equal(newFilename, filename); assert.equal(newMime, mimeType); assert.deepEqual(newImage, image);
  assert.notEqual(await pictureDigest(oldImage.data_url), await pictureDigest(newImage.data_url));
  assert.equal(old.image.data_url, dataUrl([1, 2, 3]));
});

test("reference boundary excludes unrelated Workspace fields, omits canonical pixels, retries failed writes", async () => {
  const context = { saved: true, projectId: "p", generationId: "g", projectFingerprint: "old", creativeRequest: "Unsaved" };
  const references = { assets: [asset()], subjects: [{ id: "subject-1", number: 1 }], nextSubjectNumber: 2 };
  const calls = []; let fail = false;
  const authority = createReferenceAuthority({ getContext: () => context, getReferences: () => references,
    api: { syncReferenceAssets: async (p, g, body) => { calls.push(body); if (fail) throw new Error("Offline");
      return { projectFingerprint: `saved-${calls.length}`, changed: true }; } },
    onMutation: response => { context.projectFingerprint = response.projectFingerprint; } });
  await authority.sync();
  assert.deepEqual(Object.keys(calls[0]).sort(), ["assets", "expectedProjectFingerprint", "subjects"]);
  await authority.sync();
  assert.equal(calls.length, 1, "authoritatively unchanged collection skips the write");
  references.assets[0].notes = "Changed notes";
  await authority.sync();
  assert.equal(calls[1].assets[0].image.data_url, undefined);
  assert.equal(calls[1].assets[0].imageSha256, await pictureDigest(references.assets[0].image.data_url));
  references.assets[0].image.data_url = dataUrl([9, 8, 7]); fail = true;
  await assert.rejects(authority.sync(), /Offline/);
  assert.equal(context.projectFingerprint, "saved-2");
  fail = false; await authority.sync();
  assert.equal(calls[3].assets[0].image.data_url, dataUrl([9, 8, 7]));
  assert.equal(calls[3].expectedProjectFingerprint, "saved-2");
});

test("Reference sync uses a known same-tab mutation receipt adopted while Picture hashing yields", async t => {
  const context = { saved: true, projectId: "p", generationId: "g", projectFingerprint: "F1", projectEditFingerprint: "E1", navigationTicket: { id: 1 } };
  let finish, started;
  const entered = new Promise(resolve => { started = resolve; });
  const late = new Promise(resolve => { finish = resolve; });
  const digest = crypto.subtle.digest.bind(crypto.subtle);
  t.mock.method(crypto.subtle, "digest", async (...args) => { started(); await late; return digest(...args); });
  const calls = [];
  const authority = createReferenceAuthority({ getContext: () => context,
    getReferences: () => ({ assets: [asset()], subjects: [] }),
    api: { syncReferenceAssets: async (p, g, body) => { calls.push(body); return {}; } }, onMutation() {} });
  const pending = authority.sync(); await entered;
  context.projectFingerprint = "F2"; finish(); await pending;
  assert.equal(calls.length, 1);
  assert.equal(calls[0].expectedProjectFingerprint, "F2");
});

test("Reference hashing cannot overwrite a superseding edit-authority snapshot", async t => {
  const context = { saved: true, projectId: "p", generationId: "g", projectFingerprint: "F1", projectEditFingerprint: "E1" };
  let release, started;
  const entered = new Promise(resolve => started = resolve), late = new Promise(resolve => release = resolve);
  const digest = crypto.subtle.digest.bind(crypto.subtle);
  t.mock.method(crypto.subtle, "digest", async (...args) => { started(); await late; return digest(...args); });
  const authority = createReferenceAuthority({ getContext: () => context, getReferences: () => ({ assets: [asset()], subjects: [] }),
    api: { syncReferenceAssets: () => assert.fail("superseded snapshot cannot write") }, onMutation() {} });
  const pending = authority.sync(); await entered;
  context.projectFingerprint = "F2"; context.projectEditFingerprint = "E2"; release();
  await assert.rejects(pending, /Project edits changed/);
});

test("Reference sync rejects a same-ID navigation replacement during its request", async () => {
  const context = { saved: true, projectId: "p", generationId: "g", projectFingerprint: "F1", navigationTicket: { id: 1 } };
  let finish, started;
  const entered = new Promise(resolve => { started = resolve; });
  const mutations = [];
  const authority = createReferenceAuthority({ getContext: () => context,
    getReferences: () => ({ assets: [], subjects: [] }),
    api: { syncReferenceAssets: () => { started(); return new Promise(resolve => { finish = resolve; }); } },
    onMutation: response => mutations.push(response) });
  const pending = authority.sync(); await entered;
  context.navigationTicket = { id: 2 }; context.projectFingerprint = "current-reload";
  finish({ projectFingerprint: "old-response" });
  await assert.rejects(pending, /active Generation changed/);
  assert.deepEqual(mutations, []);
  assert.equal(context.projectFingerprint, "current-reload");
});

test("queued Reference sync cannot migrate its intent to a superseding navigation", async () => {
  const context = { saved: true, projectId: "p", generationId: "g", projectFingerprint: "f", navigationTicket: { id: 1 } };
  let release, started;
  const entered = new Promise(resolve => started = resolve), calls = [];
  const authority = createReferenceAuthority({ getContext: () => context, getReferences: () => ({ assets: [], subjects: [] }),
    api: { syncReferenceAssets: async (p, g) => { calls.push([p, g]); started(); return new Promise(resolve => release = resolve); } }, onMutation() {} });
  const first = authority.sync(); await entered;
  const second = authority.sync();
  const rejected = Promise.allSettled([first, second]);
  context.navigationTicket = { id: 2 }; context.generationId = "new";
  release({});
  assert.ok((await rejected).every(result => result.status === "rejected"));
  assert.deepEqual(calls, [["p", "g"]]);
});

async function mountedAssets({ identity = false } = {}) {
  const app = await readFile(new URL("../../web/js/app.js", import.meta.url), "utf8");
  const doc = { createElement: tag => { const node = new Element(tag, doc); node.dataset = {};
    node.classList = { toggle() {}, add() {} };
    node.insertBefore = (child, before) => { child.parentElement = node; node.children.splice(node.children.indexOf(before), 0, child); };
    if (tag === "canvas") {
      node.getContext = () => ({ drawImage: (source, x, y, width, height) => {
        assert.equal(x, 0); assert.equal(y, 0); assert.equal(width, 1600); assert.equal(height, 1200);
        node.source = source;
      } });
      node.toDataURL = (mime, quality) => {
        assert.equal(mime, "image/jpeg"); assert.equal(quality, .88);
        return processed(node.source.bytes);
      };
    }
    return node; } };
  const list = new Element("div", doc), picker = new Element("input", doc);
  const state = { assets: [asset()], subjects: [], nextSubjectNumber: 1, mode: "Ref2VA", activeProjectType: "filesystem",
    currentProjectId: "p", activeGenerationId: "g", dirty: false };
  if (identity) {
    state.assets[0].role = "subject_identity";
    state.subjects = [{ id: "subject-1", number: 1 }];
    state.projectSequence = { subjects: structuredClone(state.subjects), nextSubjectNumber: 2 };
    state.nextSubjectNumber = 2;
  }
  const calls = [], toasts = []; let canonical = structuredClone(state.assets), finish, fail = false;
  const authority = createReferenceAuthority({ getContext: () => ({ saved: state.activeProjectType === "filesystem", projectId: "p", generationId: "g", projectFingerprint: "fingerprint" }),
    getReferences: () => ({ assets: state.assets, subjects: state.subjects }), onMutation: () => {
      if (!identity) return;
      for (const subject of state.subjects) {
        if (!state.projectSequence.subjects.some(s => s.id === subject.id)) state.projectSequence.subjects.push({ ...subject });
      }
      state.projectSequence.nextSubjectNumber = Math.max(state.projectSequence.nextSubjectNumber, state.nextSubjectNumber);
    },
    api: { syncReferenceAssets: async (_p, _g, body) => {
      calls.push(body); await new Promise(resolve => { finish = resolve; });
      if (fail) throw new Error("Save rejected"); canonical = structuredClone(state.assets); return { changed: true }; } } });
  const scope = { state, document: doc, structuredClone, replacePicture, Blob, mediaPayloadStatus,
    Option: function(text, value) { const option = new Element("option", doc); option.textContent = text; option.value = value; return option; },
    $: selector => selector === "#asset-file-input" ? picker : list,
    $$: () => list.querySelectorAll("button, input, select, textarea"),
    normalizeSubjectsInState: () => {}, isSubjectIdentityAsset: () => false, isComparisonAsset: () => false,
    roleCatalog: { image: [["environment", "Environment"], ["general_visual", "General"]] },
    setDirty: value => { state.dirty = value ?? true; }, toast: value => toasts.push(value), renderAssetActions: () => {},
    uid: () => "new-slot", defaultSubjectIdentity: () => ({}),
    createImageBitmap: async file => ({ width: 3200, height: 2400, bytes: file.bytes, close() {} }),
    FileReader: class { readAsDataURL(file) { this.result = dataUrl(file.bytes); this.onload(); } },
  };
  if (identity) Object.assign(scope, identityHelpers, {
    subjectIdentityPresentation,
    roleCatalog: { image: [["subject_identity", "Subject identity"], ["environment", "Environment"]] },
    normalizeSubjectsInState: () => Object.assign(state, identityHelpers.normalizeSubjectRegistry(
      state.subjects, state.assets, state.nextSubjectNumber, state.projectSequence)),
  });
  picker.click = () => { picker.clicked = true; };
  scope.renderAssets = () => { list.replaceChildren(scope.assetCard(state.assets[0], "Picture 1")); };
  const start = app.indexOf("let referenceAuthority = null"), end = app.indexOf("async function runConnection(");
  runInNewContext(app.slice(start, end) + "\nfunction installAuthority(value) { referenceAuthority = value; }\nfunction pendingMutation() { return referenceMutation; }", scope);
  scope.installAuthority(authority);
  scope.renderAssets = () => { list.replaceChildren(...state.assets.map(item => scope.assetCard(item, "Picture 1"))); };
  scope.renderAssets();
  return { scope, list, picker, state, calls, toasts, canonical: () => canonical,
    fail: () => { fail = true; }, release: async () => { while (!finish) await new Promise(resolve => setImmediate(resolve)); finish(); finish = null; } };
}

test("mounted Remove waits for canonical success; failure restores card and reports error", async () => {
  for (const failing of [false, true]) {
    const s = await mountedAssets();
    if (failing) s.fail();
    const remove = s.list.querySelectorAll("button").find(button => button.textContent === "Remove");
    await remove.emit("click");
    assert.equal(remove.disabled, true);
    assert.equal(s.list.querySelectorAll(".asset-card").length, 1, "old canonical card remains until success");
    const pending = s.scope.pendingMutation(); await s.release();
    if (failing) await assert.rejects(pending, /Save rejected/); else await pending;
    assert.equal(s.list.querySelectorAll(".asset-card").length, failing ? 1 : 0);
    assert.equal(s.canonical().length, failing ? 1 : 0);
    assert.equal(s.state.dirty, false);
    if (failing) assert.match(s.toasts[0], /Save rejected/);
  }
});

test("mounted Replace uses the normal single-file picker and adopts pixels only after canonical success", async () => {
  for (const failing of [false, true]) {
    const s = await mountedAssets();
    if (failing) s.fail();
    const replace = s.list.querySelectorAll("button").find(button => button.textContent === "Replace Picture");
    await replace.emit("click");
    assert.equal(s.picker.accept, "image/*"); assert.equal(s.picker.multiple, false); assert.equal(s.picker.clicked, true);
    const ingest = s.scope.ingestAssetFiles([{ name: "outfit.png", type: "image/png", size: 3, bytes: [7, 8, 9] }], "image", "slot");
    assert.equal(s.list.querySelector("img").src, dataUrl([1, 2, 3]));
    await s.release(); await ingest;
    const expected = failing ? dataUrl([1, 2, 3]) : processed([7, 8, 9]);
    assert.equal(s.list.querySelector("img").src, expected);
    assert.equal(s.state.assets[0].id, "slot"); assert.equal(s.state.assets[0].notes, "Preserve these notes");
    assert.equal(s.state.assets[0].image.width, failing ? 1 : 1600); assert.equal(s.state.dirty, false);
    assert.equal(s.canonical()[0].image.data_url, expected);
    if (!failing) {
      assert.equal(s.state.assets[0].mimeType, "image/jpeg");
      assert.notEqual(await pictureDigest(expected), await pictureDigest(dataUrl([1, 2, 3])));
      assert.notEqual(expected, dataUrl([7, 8, 9]), "processed evidence differs from source bytes");
    }
    if (failing) assert.match(s.toasts[0], /Save rejected/);
  }
});

test("mounted Add saves reference bytes without saving unrelated dirty authoring", async () => {
  const s = await mountedAssets(); s.state.dirty = true;
  const ingest = s.scope.ingestAssetFiles([{ name: "outfit.png", type: "image/png", size: 3, bytes: [7, 8, 9] }], "image");
  await s.release(); await ingest;
  assert.equal(s.canonical().length, 2);
  assert.equal(s.canonical()[1].id, "new-slot");
  assert.equal(s.canonical()[1].image.data_url, processed([7, 8, 9]));
  assert.equal(s.state.dirty, true);
  assert.deepEqual(Object.keys(s.calls[0]).sort(), ["assets", "expectedProjectFingerprint", "subjects"]);
});

test("mounted Fresh-copy Picture removal retains dormant identity; new and attach actions stay distinct", async () => {
  for (const attach of [false, true]) {
    const s = await mountedAssets({ identity: true });
    const remove = s.list.querySelectorAll("button").find(button => button.textContent === "Remove");
    await remove.emit("click");
    const pending = s.scope.pendingMutation(); await s.release(); await pending;
    assert.deepEqual(s.state.subjects, []);
    assert.deepEqual(s.state.projectSequence, { subjects: [{ id: "subject-1", number: 1 }], nextSubjectNumber: 2 });
    assert.deepEqual(s.calls[0].assets, []);
    assert.deepEqual(s.calls[0].subjects, []);
    s.scope.chooseAsset("image", null, attach ? "subject-1" : null);
    assert.equal(s.state.pendingAssetSubjectId, attach ? "subject-1" : null);
    const added = s.scope.ingestAssetFiles([{ name: "character.png", type: "image/png", size: 3, bytes: [7, 8, 9] }],
      s.state.pendingAssetKind, s.state.reattachId, s.state.pendingAssetSubjectId);
    await s.release(); await added;
    const subject = s.state.subjects[0];
    assert.equal(subject.number, attach ? 1 : 2);
    assert.equal(subject.id === "subject-1", attach);
    assert.equal(s.state.assets[0].subjectIdentity.subjectId, subject.id);
    assert.equal(s.calls[1].subjects[0].number, subject.number);
    assert.equal(s.state.projectSequence.subjects[0].id, "subject-1");
    assert.equal(s.state.projectSequence.nextSubjectNumber, attach ? 2 : 3);
    assert.equal(s.state.pendingAssetSubjectId, null);
  }
});

async function storyGate(s) {
  const app = await readFile(new URL("../../web/js/app.js", import.meta.url), "utf8");
  const feedback = {};
  const noop = () => {};
  Object.assign(s.scope, {
    AbortController, storyGenerationFeedback,
    $: () => feedback,
    providerActivity: { active: () => null, finish: noop }, providerAttempt: () => ({}),
    generationClickIntent: () => "start", providerContextEpoch: 0,
    markRevisionStale: noop, persistSettings: noop,
    // Stop after actual eligibility + reference synchronization, before any writer call.
    ensureDialogueResolution: async () => { throw new Error("Passed Story gate"); },
  });
  s.state.scenePlan = s.state.savedScenePlan = "Saved scene";
  runInNewContext(app.slice(app.indexOf("async function generate(authoringPath"), app.indexOf("function renderWarnings(")), s.scope);
  const pending = s.scope.generate("story");
  if (!s.state.dirty && s.state.activeProjectType === "filesystem") await s.release();
  await pending;
  return s.toasts.at(-1);
}

test("saved Notes remain clean, persist, and pass the actual Story H3 gate", async () => {
  const s = await mountedAssets();
  const notes = s.list.querySelector("textarea");
  notes.value = "New reference facts"; await notes.emit("input");
  assert.equal(s.state.assets[0].notes, notes.value);
  assert.equal(s.state.dirty, false);
  await notes.emit("change"); const pending = s.scope.pendingMutation();
  await s.release(); await pending;
  assert.equal(s.canonical()[0].notes, "New reference facts");
  assert.equal(s.state.dirty, false);
  const latestNotes = s.list.querySelector("textarea");
  latestNotes.value = "Latest facts before field completion"; await latestNotes.emit("input");
  assert.equal(await storyGate(s), "Passed Story gate");
  assert.equal(s.canonical()[0].notes, "Latest facts before field completion");
  assert.equal(s.state.dirty, false);
});

test("Notes preserve unrelated authoring dirtiness and the Story save gate", async () => {
  const s = await mountedAssets(); s.state.dirty = true;
  const notes = s.list.querySelector("textarea");
  notes.value = "Reference edit"; await notes.emit("input"); await notes.emit("change");
  const pending = s.scope.pendingMutation(); await s.release(); await pending;
  assert.equal(s.canonical()[0].notes, "Reference edit");
  assert.equal(s.state.dirty, true);
  assert.match(await storyGate(s), /Save the current Scene Plan/);
});

test("unsaved Notes mark the local Project dirty without a filesystem write", async () => {
  const s = await mountedAssets(); s.state.activeProjectType = "local";
  const notes = s.list.querySelector("textarea");
  notes.value = "Local reference edit"; await notes.emit("input");
  assert.equal(s.state.dirty, true);
  await notes.emit("change"); await s.scope.pendingMutation();
  assert.equal(s.state.assets[0].notes, "Local reference edit"); assert.equal(s.calls.length, 0);
});

test("failed Notes save retains retryable local text, reports error, and marks unsaved", async () => {
  const s = await mountedAssets(); s.fail();
  const notes = s.list.querySelector("textarea");
  notes.value = "Retry these facts"; await notes.emit("input"); await notes.emit("change");
  const pending = s.scope.pendingMutation(); await s.release(); await assert.rejects(pending, /Save rejected/);
  assert.equal(s.state.assets[0].notes, "Retry these facts");
  assert.equal(s.canonical()[0].notes, "Preserve these notes");
  assert.equal(s.state.dirty, true); assert.match(s.toasts[0], /Could not save Reference Assets: Save rejected/);
});


test("unavailable Picture sync carries historical digest without pixels; Replace restores explicit pixels", async () => {
  const broken = { ...asset(), image: null, payloadAvailability: "corrupt", payloadSha256: "a".repeat(64) };
  const calls = [];
  const refs = { assets: [broken], subjects: [] };
  const authority = createReferenceAuthority({
    getContext: () => ({ saved: true, projectId: "p", generationId: "g", projectFingerprint: "current" }),
    getReferences: () => refs, onMutation: () => {},
    api: { syncReferenceAssets: async (_p, _g, body) => { calls.push(body); return {}; } },
  });
  await authority.sync();
  assert.equal(calls[0].assets[0].image, null);
  assert.equal(calls[0].assets[0].imageSha256, broken.payloadSha256);
  refs.assets[0] = replacePicture(broken, { name: "replacement.png" }, asset().image);
  await authority.sync();
  assert.equal(calls[1].assets[0].image.data_url, asset().image.data_url);
  assert.equal(calls[1].assets[0].payloadAvailability, null);
});


test("mounted unavailable Picture retains recovery controls without fabricated preview pixels", async () => {
  for (const damage of ["missing", "corrupt"]) {
    for (const operation of ["remove", "replace"]) {
      const s = await mountedAssets();
      Object.assign(s.state.assets[0], { payloadSha256: await pictureDigest(s.state.assets[0].image.data_url),
        image: null, attached: false, payloadAvailability: damage });
      s.scope.renderAssets();
      assert.equal(s.list.querySelector("img"), null);
      assert.equal(s.list.querySelector(".asset-preview").textContent, "Picture unavailable");
      assert.ok(s.list.querySelectorAll("button").some(button => button.textContent === "Replace Picture"));
      const remove = s.list.querySelectorAll("button").find(button => button.textContent === "Remove");
      if (operation === "remove") {
        await remove.emit("click");
        const pending = s.scope.pendingMutation(); await s.release(); await pending;
        assert.equal(s.state.assets.length, 0);
      } else {
        const pending = s.scope.ingestAssetFiles([{ name: "new.png", type: "image/png", size: 3, bytes: [7, 8, 9] }], "image", "slot");
        assert.equal(s.list.querySelector("img"), null);
        await s.release(); await pending;
        assert.equal(s.list.querySelector("img").src, processed([7, 8, 9]));
        assert.equal(s.state.assets[0].payloadAvailability, null);
      }
    }
  }
});
