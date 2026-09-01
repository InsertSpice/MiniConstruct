import assert from "node:assert/strict";
import test from "node:test";

import { canStartRepair, repairUiState } from "../../web/js/repair-state.js";

test("repair state disables immediately and exposes progress", () => {
  assert.deepEqual(repairUiState({ hasOutput: true, generating: false, repairRunning: true }), {
    disabled: true, label: "Repairing…", busy: true,
  });
});

test("repair guard rejects re-entry and restores a retryable idle state", () => {
  assert.equal(canStartRepair(false), true);
  assert.equal(canStartRepair(true), false);
  assert.deepEqual(repairUiState({ hasOutput: true, generating: false, repairRunning: false }), {
    disabled: false, label: "Repair Format", busy: false,
  });
});
