import assert from "node:assert/strict";
import test from "node:test";

import { createEventStreamParser, snapshotGenerationRequest } from "../../web/js/streaming.js";


test("SSE parser handles arbitrary byte boundaries and distinct variations", () => {
  const events = [];
  const parser = createEventStreamParser(event => events.push(event));
  parser.push('event: delta\r\ndata: {"variation":0,"text":"sub');
  parser.push('ject_"}\r\n\r\nevent: delta\ndata: {"variation":1,"text":"audio"}\n\n');
  parser.push('event: done\ndata: {"variations":2}\n\n');
  parser.finish();
  assert.deepEqual(events, [
    { event: "delta", data: { variation: 0, text: "subject_" } },
    { event: "delta", data: { variation: 1, text: "audio" } },
    { event: "done", data: { variations: 2 } },
  ]);
});


test("generation request snapshot is unaffected by later workspace edits", () => {
  const visible = {
    workspace: { creativeRequest: "Snapshot A", assets: [{ notes: "original" }] },
    llm: { modelId: "writer" },
  };
  const snapshot = snapshotGenerationRequest(visible);
  visible.workspace.creativeRequest = "Visible Workspace B";
  visible.workspace.assets[0].notes = "edited later";
  visible.llm.modelId = "different model";
  assert.deepEqual(snapshot, {
    workspace: { creativeRequest: "Snapshot A", assets: [{ notes: "original" }] },
    llm: { modelId: "writer" },
  });
});
