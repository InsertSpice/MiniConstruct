import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

test("every declared purple primary action is attemptable by construction", async () => {
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  const css = await readFile(new URL("../../web/css/app.css", import.meta.url), "utf8");
  const scripts = ["app.js", "story-planning.js", "workflow-preparation.js"];
  const markup = [...html.matchAll(/<button\b[^>]*\bclass="[^"]*\bprimary\b[^"]*"[^>]*>/g)].map(match => match[0]);
  assert.ok(markup.length >= 5);
  const app = await readFile(new URL("../../web/js/app.js", import.meta.url), "utf8");
  for (const tag of markup) {
    assert.doesNotMatch(tag, /\sdisabled(?:\s|=|>)/i, tag);
    assert.doesNotMatch(tag, /aria-disabled="true"/i, tag);
    assert.doesNotMatch(tag, /pointer-events\s*:\s*none/i, tag);
    const id = tag.match(/\bid="([^"]+)"/)?.[1];
    assert.ok(id, tag);
    assert.doesNotMatch(app, new RegExp(`\\$\\(\\s*["']#${id}["']\\s*\\)\\.disabled\\s*=`), id);
  }
  let dynamicCount = 0;
  for (const name of scripts) {
    const source = await readFile(new URL(`../../web/js/${name}`, import.meta.url), "utf8");
    for (const match of source.matchAll(/\b(\w+)\.className\s*=\s*"primary"/g)) {
      dynamicCount++;
      const variable = match[1];
      assert.doesNotMatch(source, new RegExp(`\\b${variable}\\.disabled\\s*=`), `${name}: ${variable}`);
      assert.doesNotMatch(source, new RegExp(`\\b${variable}\\.setAttribute\\(\\s*["']aria-disabled`), `${name}: ${variable}`);
    }
  }
  assert.ok(dynamicCount >= 10, "Story and execution actions must be included");
  assert.doesNotMatch(css, /(?:button\.primary|\.primary)(?::disabled|\.disabled|[^{}]*pointer-events\s*:\s*none)/);
  assert.match(css, /button:not\(\.primary\):disabled/);
});
