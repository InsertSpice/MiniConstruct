import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, existsSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { spawn, execFileSync } from "node:child_process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

// Optional browser runtime; the DOM suite remains portable without a browser installation.
const require = createRequire(import.meta.url);
let chromium;
try { ({ chromium } = require("playwright")); } catch {
  const bundled = join(process.env.USERPROFILE || "", ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
  try { ({ chromium } = require(bundled)); } catch {}
}
const root = fileURLToPath(new URL("../../", import.meta.url));
const browserExecutable = chromium && [chromium.executablePath(),
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe"].find(existsSync);
const head = process.env.MC_RATIO_TEST_HEAD === "1";
const source = path => head ? execFileSync("git", ["show", `HEAD:${path}`], { cwd: root, encoding: "utf8" }) : readFileSync(join(root, path), "utf8");

for (const scenario of ["legacy", "modern", "saved-stale", "modern-discovery"]) {
const discovery = scenario === "modern-discovery", legacy = scenario === "legacy" || scenario === "saved-stale", stale = scenario === "saved-stale";
test(`native browser ${scenario} H3 ratios exact mocked /prompt`, { skip: !browserExecutable }, async t => {
  const python = join(root, ".venv/Scripts/python.exe");
  const child = spawn(existsSync(python) ? python : "python", [join(root, "tests/js/support/project-fingerprint.py"), ...(legacy ? ["--legacy"] : []), ...(stale ? ["--stale"] : []), ...(discovery ? ["--discovery"] : [])]);
  t.after(() => child.stdin.end());
  const pending = []; let stderr = "";
  child.stderr.on("data", data => { stderr += data; });
  createInterface({ input: child.stdout }).on("line", line => {
    const { result, error } = JSON.parse(line), task = pending.shift();
    if (error) task.reject(new Error(error)); else task.resolve(result);
  });
  child.on("exit", () => { for (const task of pending.splice(0)) task.reject(new Error(stderr || "Bridge exited")); });
  const request = (op, generationId, body) => new Promise((resolve, reject) => {
    pending.push({ resolve, reject }); child.stdin.write(JSON.stringify({ op, generationId, body }) + "\n");
  });
  const initial = await request("init");
  assert.equal(initial.profile.bindings.some(binding => binding.source.kind === "aspect_ratio"), !legacy);
  if (legacy) {
    const declared = initial.profile.exposedControls.find(control => control.id === "aspect_ratio");
    assert.deepEqual(declared.target, { nodeId: "115", inputName: "aspect_ratio", path: [] });
    assert.equal(declared.defaultValue, "4:3 (Standard)");
  }
  const browser = await chromium.launch({ headless: true, executablePath: browserExecutable }); t.after(() => browser.close());
  const page = await browser.newPage();
  await page.route("**/*", route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.startsWith("/web/js/") && /^\/web\/js\/[a-z-]+\.js$/.test(pathname))
      return route.fulfill({ contentType: "text/javascript", body: source(pathname.slice(1)) });
    if (pathname === "/") return route.fulfill({ contentType: "text/html", body: source("web/index.html").replace(/<script[\s\S]*?<\/script>/g, "").replace(/<link[^>]*>/g, "") });
    return route.abort(); // No provider, ComfyUI, or other network access.
  });
  await page.exposeFunction("bridge", request);
  await page.goto("http://ratio-fixture.test/");
  assert.ok(await page.locator('#aspect-ratio option').evaluateAll(options => options.some(option => option.value === "3:2")));
  const app = source("web/js/app.js").replaceAll("\r\n", "\n");
  const contextStart = app.indexOf("    getContext: () =>", app.indexOf("  workflowPreparation = createWorkflowPreparation"));
  const context = app.slice(contextStart + "    getContext: ".length, app.indexOf(",\n    getOutput:", contextStart));
  const wiring = app.split("\n").filter(line => line.includes('$("#aspect-ratio").addEventListener("change"') || line.includes('document.addEventListener("input", event => { if (shouldMarkWorkspaceDirty')).join("\n");
  const dirty = app.slice(app.indexOf("function setDirty("), app.indexOf("function captureContinuity("));
  await page.evaluate(async ({ initial, context, wiring, dirty, stale, discovery }) => {
    const { createWorkflowPreparation } = await import("/web/js/workflow-preparation.js");
    const { shouldMarkWorkspaceDirty } = await import("/web/js/sequence-workflow.js");
    const $ = selector => document.querySelector(selector);
    const state = { activeProjectType: "filesystem", currentProjectId: initial.projectId, activeGenerationId: initial.generationId,
      projectFingerprint: initial.projectFingerprint, mode: "T2VA", contextReplacing: false, dirty: false };
    const projectMutationContext = () => ({});
    const renderedTakes = null, storyPlanner = null; const renderSequenceControls = () => {};
    $("#duration").value = "8"; $("#aspect-ratio").value = "4:3";
    const getContext = eval(`(${context})`);
    const setupCalls = [];
    const api = {
      executionProfiles: async () => [(stale || discovery) ? await window.bridge("profileGet") : initial.profile],
      executionProfile: async () => (stale || discovery) ? window.bridge("profileGet") : initial.profile,
      workflowControls: (p,g) => window.bridge("controls",g),
      executionConnections: async () => [initial.connection], executionSelection: (p,g) => window.bridge("selection",g),
      selectExecutionPrompt: (p,g,b) => window.bridge("select",g,b),
      adoptManualEditExecutionPrompt: (p,g,b) => window.bridge("adopt",g,b),
      updateExecutionSetup: (p,g,b) => { setupCalls.push(b); return window.bridge("duration",g,b); },
      prepareWorkflow: b => window.bridge("prepare",b.generationId), sendExecution: b => window.bridge("send",b.generationId,b),
    };
    const workflowPreparation = createWorkflowPreparation({ root: $("#workflow-preparation-primary"), api, getContext,
      getOutput: () => ({ prompt: initial.prompt, sourceFingerprint: initial.sourceFingerprint, origin: "generated_prompt" }),
      onProjectMutation: result => { state.projectFingerprint = result.projectFingerprint; },
      onSubmitted: result => { state.projectFingerprint = result.projectFingerprint; } });
    eval(dirty + "\n" + wiring);
    window.ratioTest = { workflowPreparation, state, setupCalls, getContext };
    await workflowPreparation.refresh();
    if (stale || discovery) {
      const { createExecutionConfig } = await import("/web/js/execution-config.js");
      const editor = document.createElement("div"); document.body.append(editor);
      const config = createExecutionConfig({ root: editor, api: { ...api,
        executionCapabilities: async () => ({ compatibleModes: ["T2VA"], assetKinds: ["image"], maxWorkflowBytes: 2097152 }),
        executionEnumCapabilities: (id,controlId,connectionId) => window.bridge("enumCapabilities", initial.generationId, { connectionId }),
        executionAspectRatioCapabilities: (id,nodeId,inputName,connectionId) => window.bridge("aspectCapabilities", initial.generationId, { nodeId,inputName,connectionId }),
        saveExecutionProfile: (id,body) => window.bridge("profileSave", initial.generationId, body),
      }, onProfilesChanged: () => workflowPreparation.refresh() });
      await config.refresh();
    }
  }, { initial, context, wiring, dirty, stale, discovery });
  if (discovery) {
    assert.equal(Object.keys(initial.profile.bindings.find(b => b.source.kind === "aspect_ratio").source.valueMap).length, 6);
    assert.deepEqual(await page.locator('#aspect-ratio option').evaluateAll(options => options.map(o => o.value)),
      ["auto", "16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9", "custom"]);
    await page.click(`#execution-profile-edit-${initial.profile.id}`);
    const index = initial.profile.bindings.findIndex(b => b.source.kind === "aspect_ratio");
    await page.click(`#execution-binding-edit-${index}`);
    await page.selectOption("#execution-enum-connection", initial.connection.id);
    await page.fill("#execution-aspect-map-5", "4:3 (Standard)");
    await page.click("#execution-aspect-populate");
    await page.waitForFunction(() => !document.querySelector("#execution-aspect-apply").hidden);
    assert.equal(Object.keys((await request("profileGet")).bindings[index].source.valueMap).length, 6);
    assert.match(await page.locator("#execution-aspect-mapping").textContent(), /conflicts with H3 Setup 3:2/);
    assert.match(await page.locator("#execution-aspect-mapping").textContent(), /Replace with “3:2 \(Photo\)”/);
    await page.click("#execution-aspect-apply");
    assert.match(await page.locator("#execution-binding-status").textContent(), /Decide each conflicting/);
    assert.equal(await page.locator("#execution-aspect-map-5").inputValue(), "4:3 (Standard)");
    await page.locator('#execution-aspect-mapping input[type="checkbox"]').check();
    await page.click("#execution-aspect-apply");
    await page.click("#execution-profile-save");
    await page.waitForFunction(() => document.querySelector("#execution-profile-status").textContent.includes("Workflow Profile saved"));
    const saved = await request("profileGet");
    assert.equal(Object.keys(saved.bindings[index].source.valueMap).length, 8);
    assert.deepEqual(saved.exposedControls, []);
    for (const [ratio,value] of Object.entries(initial.profile.bindings[index].source.valueMap)) assert.equal(saved.bindings[index].source.valueMap[ratio], value);
  }
  const prepare = async () => {
    // Wait for the visible status read started by the event, without calling sync from the test.
    await page.waitForFunction(() => !document.querySelector("#preparation-selection").textContent.startsWith("Loading"));
    await page.click("#preparation-prepare"); await page.waitForFunction(() => Boolean(window.ratioTest.workflowPreparation.prepared)); };
  const send = async () => { await page.click("#preparation-send"); await page.waitForFunction(() => document.querySelector("#preparation-receipt").textContent.includes("Queued in ComfyUI")); };
  await prepare(); await send();
  assert.equal((await request("submitted"))[0].prompt[initial.ratioNodeId].inputs.aspect_ratio, "4:3 (Standard)");
  if (stale) {
    assert.deepEqual(initial.profile.exposedControls[0].choices, ["4:3 (Standard)", "16:9 (Widescreen)"]);
    await page.selectOption("#aspect-ratio", "3:2");
    await page.click("#preparation-prepare");
    await page.waitForFunction(() => document.querySelector("#preparation-message").textContent.includes("missing declared choice"));
    assert.equal(await page.evaluate(() => window.ratioTest.workflowPreparation.prepared), null);
    await page.click(`#execution-profile-edit-${initial.profile.id}`);
    await page.selectOption("#execution-enum-connection", initial.connection.id);
    await page.click("#execution-control-sync-0");
    await page.waitForFunction(() => document.querySelector("#execution-control-status").textContent.includes("Nothing has been saved"));
    assert.deepEqual((await request("profileGet")).exposedControls[0].choices, initial.profile.exposedControls[0].choices);
    await page.click("#execution-profile-save");
    await page.waitForFunction(() => document.querySelector("#execution-profile-status").textContent.includes("Workflow Profile saved"));
    await prepare();
    assert.equal(await page.evaluate(() => window.ratioTest.workflowPreparation.prepared.graph["115"].inputs.aspect_ratio), "3:2 (Photo)");
    await request("load");
    await page.evaluate(() => window.ratioTest.workflowPreparation.refresh());
    await prepare();
    assert.equal(await page.evaluate(() => window.ratioTest.workflowPreparation.prepared.graph["115"].inputs.aspect_ratio), "3:2 (Photo)");
  }
  await page.selectOption("#aspect-ratio", "9:16");
  const changed = await page.evaluate(() => ({ prepared: window.ratioTest.workflowPreparation.prepared,
    disabled: document.querySelector("#preparation-send").disabled, context: window.ratioTest.getContext() }));
  assert.equal(changed.prepared, null); assert.equal(changed.context.aspectRatio, "9:16"); assert.equal(changed.context.dirty, true);
  assert.equal(changed.disabled, false);
  await prepare();
  const prepared = await page.evaluate(() => window.ratioTest.workflowPreparation.prepared);
  // This result comes from the real Python compiler through the mounted handlers.
  assert.equal(await page.locator("#preparation-status").textContent(), "Prepared \u2014 Ready to Send");
  assert.equal(await page.locator(".preparation-warnings").count(), 0);
  const preparationDetails = page.locator(".preparation-details");
  assert.equal(await preparationDetails.evaluate(node => node.open), false);
  assert.equal(await preparationDetails.locator("p").first().isVisible(), false);
  await preparationDetails.locator("summary").focus();
  await preparationDetails.locator("summary").press("Enter");
  assert.equal(await preparationDetails.evaluate(node => node.open), true);
  assert.equal(await preparationDetails.locator("p").first().isVisible(), true);
  assert.match(await preparationDetails.textContent(), /Prepared offline according to profile semantics/);
  assert.equal(await page.locator("#preparation-status").getAttribute("role"), "status");

  if (legacy) {
    assert.ok(prepared.information.some(info => info.includes("is driven by H3 Setup")));
    assert.equal(await page.locator("#preparation-control-aspect_ratio").isVisible(), false);
  }
  assert.equal(prepared.graph[initial.ratioNodeId].inputs.aspect_ratio, "9:16 (Portrait Widescreen)");
  const inspection = prepared.resolutions.find(item => item.nodeId === initial.ratioNodeId);
  assert.equal(inspection.sourceValue, "9:16"); assert.equal(inspection.rendererValue, "9:16 (Portrait Widescreen)");
  assert.equal((await request("workspace")).aspectRatio, "9:16");
  assert.equal(await page.evaluate(() => window.ratioTest.setupCalls.at(-1).aspectRatio), "9:16");
  await send(); assert.equal((await request("submitted"))[1].prompt[initial.ratioNodeId].inputs.aspect_ratio, "9:16 (Portrait Widescreen)");
  if (process.env.MC_ASPECT_EVIDENCE_ROOT) {
    writeFileSync(join(process.env.MC_ASPECT_EVIDENCE_ROOT, `${legacy ? "legacy" : "modern"}-portrait-prompt.json`), JSON.stringify((await request("submitted"))[1], null, 2));
    writeFileSync(join(process.env.MC_ASPECT_EVIDENCE_ROOT, `${legacy ? "legacy" : "modern"}-portrait-prepared.json`), JSON.stringify(prepared, null, 2));
    writeFileSync(join(process.env.MC_ASPECT_EVIDENCE_ROOT, `${legacy ? "legacy" : "modern"}-portrait-generation.json`), JSON.stringify(await request("workspace"), null, 2));
  }
  for (const [ratio, renderer] of Object.entries(initial.ratioMap)) {
    await page.selectOption("#aspect-ratio", ratio);
    assert.equal(await page.evaluate(() => window.ratioTest.getContext().aspectRatio), ratio);
    await prepare();
    assert.equal((await request("workspace")).aspectRatio, ratio);
    assert.equal(await page.evaluate(id => window.ratioTest.workflowPreparation.prepared.graph[id].inputs.aspect_ratio, initial.ratioNodeId), renderer);
    await send(); assert.equal((await request("submitted")).at(-1).prompt[initial.ratioNodeId].inputs.aspect_ratio, renderer);
  }
  if (discovery) {
    await page.selectOption("#aspect-ratio", "custom"); await page.fill("#custom-ratio", "21:9");
    await prepare(); await send();
    assert.equal((await request("submitted")).at(-1).prompt["115"].inputs.aspect_ratio, "21:9 (Ultrawide)");
    await page.fill("#custom-ratio", "5:4");
    assert.equal(await page.evaluate(() => window.ratioTest.workflowPreparation.prepared), null);
    await page.click("#preparation-prepare");
    await page.waitForFunction(() => document.querySelector("#preparation-message").textContent.includes("does not support"));
    assert.equal(await page.evaluate(() => window.ratioTest.workflowPreparation.prepared), null);
    return;
  }
  if (legacy) {
    await page.selectOption("#aspect-ratio", "custom"); await page.fill("#custom-ratio", "3:2");
    await prepare();
    assert.equal((await request("workspace")).aspectRatio, "3:2");
    assert.equal(await page.evaluate(id => window.ratioTest.workflowPreparation.prepared.graph[id].inputs.aspect_ratio, initial.ratioNodeId), "3:2 (Photo)");
    await send(); assert.equal((await request("submitted")).at(-1).prompt[initial.ratioNodeId].inputs.aspect_ratio, "3:2 (Photo)");
    for (const value of ["auto", "custom"]) {
      await page.selectOption("#aspect-ratio", value);
      if (value === "custom") await page.fill("#custom-ratio", "2:1");
      await page.click("#preparation-prepare");
      await page.waitForFunction(() => document.querySelector("#preparation-message").textContent.includes("legacy Aspect Ratio"));
      assert.equal(await page.evaluate(() => window.ratioTest.workflowPreparation.prepared), null);
    }
    return;
  }
  await page.selectOption("#aspect-ratio", "custom"); await page.fill("#custom-ratio", "2.39:1");
  assert.equal(await page.evaluate(() => window.ratioTest.workflowPreparation.prepared), null);
  await prepare(); await page.fill("#custom-ratio", "2:1");
  assert.equal(await page.evaluate(() => window.ratioTest.workflowPreparation.prepared), null);
  await prepare(); await send();
  assert.equal((await request("submitted")).at(-1).prompt[initial.ratioNodeId].inputs.aspect_ratio, "2:1");
});

}
