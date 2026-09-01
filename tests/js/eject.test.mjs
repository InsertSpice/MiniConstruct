import assert from "node:assert/strict";
import { canEjectModel } from "../../web/js/eject.js";

assert.equal(canEjectModel({ modelId: "model", generating: false, revising: false, repairing: false, ejecting: false }), true);
for (const state of [
  { modelId: "", generating: false, revising: false, repairing: false, ejecting: false },
  { modelId: "model", generating: true, revising: false, repairing: false, ejecting: false },
  { modelId: "model", generating: false, revising: true, repairing: false, ejecting: false },
  { modelId: "model", generating: false, revising: false, repairing: true, ejecting: false },
  { modelId: "model", generating: false, revising: false, repairing: false, ejecting: true },
]) assert.equal(canEjectModel(state), false);
