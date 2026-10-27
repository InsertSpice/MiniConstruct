import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, existsSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";

const require = createRequire(import.meta.url);
let chromium;
try { ({ chromium } = require("playwright")); } catch {
  const bundled = join(process.env.USERPROFILE || "", ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
  try { ({ chromium } = require(bundled)); } catch {}
}
const executablePath = chromium && [chromium.executablePath(),
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe"].find(existsSync);
const source = path => readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");

test("Save As checkbox aligns with its accessible label at desktop and narrow widths", { skip: !executablePath }, async t => {
  const browser = await chromium.launch({ headless: true, executablePath });
  t.after(() => browser.close());
  const page = await browser.newPage();
  page.setDefaultTimeout(5000);
  await page.route("**/*", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/") return route.fulfill({ contentType: "text/html", body:
      source("web/index.html").replace(/<script[\s\S]*?<\/script>/g, "").replace(/<link[^>]*>/g, "") });
    if (path === "/project-generated-state.js") return route.fulfill({ contentType: "text/javascript", body: source("web/js/project-generated-state.js") });
    return route.abort();
  });
  await page.goto("http://save-as-fixture.test/");
  await page.addStyleTag({ content: source("web/css/app.css") });
  await page.evaluate(async () => {
    const { createProjectGeneratedState } = await import("/project-generated-state.js");
    window.copies = [];
    createProjectGeneratedState({ root: document, getContext: () => ({ name: "Copy", saved: true }),
      saveAs: async options => { window.copies.push(options); return { saved: true }; },
      clearGeneratedState: async () => true, notify: () => {} });
    // The menu is normally opened by the app; this isolated fixture exposes its button.
    document.querySelector("#project-save-as").click();
  });
  const row = page.locator("#project-keep-prompts-row");
  const checkbox = page.getByRole("checkbox", { name: "Keep Saved H3 Prompts (text history only)", exact: true });
  assert.equal(await row.isVisible(), false);
  await page.locator("#project-copy-mode").selectOption("fresh");
  for (const width of [1200, 480, 360]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await row.isVisible(), true);
    const input = await checkbox.boundingBox();
    const label = await row.locator("span").boundingBox();
    assert.ok(input.width < 30, "checkbox must retain a natural width");
    assert.ok(label.x > input.x + input.width, "text follows checkbox on the same row");
    assert.ok(Math.abs(input.y + input.height / 2 - label.y - label.height / 2) < 2, "centers align even when text wraps");
  }
  await row.locator("span").click();
  assert.equal(await checkbox.isChecked(), true);
  await page.locator("#project-copy-confirm").click();
  assert.deepEqual(await page.evaluate(() => window.copies), [{ name: "Copy", copyMode: "fresh", keepSavedPrompts: true }]);
  await page.evaluate(() => document.querySelector("#project-save-as").click());
  assert.equal(await row.isVisible(), false);
  assert.equal(await page.locator("#project-keep-prompts").isChecked(), false);
  await page.locator("#project-copy-cancel").click();
  assert.equal(await page.locator("#project-save-as-dialog").isVisible(), false);
});
