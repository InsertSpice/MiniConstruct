import assert from "node:assert/strict";
import test from "node:test";

import {
  SEED_MAX, normalizeSeedSettings, resolveGenerationSeeds, resolveSeed,
  secureRandomSeed, snapshotGenerationRequest,
} from "../../web/js/seed.js";

test("seed settings default safely and fixed values persist without shared state", () => {
  assert.deepEqual(normalizeSeedSettings(), { seedMode: "backend_default", fixedSeed: null });
  assert.deepEqual(normalizeSeedSettings({ seedMode: "fixed", fixedSeed: "3407" }), { seedMode: "fixed", fixedSeed: 3407 });
  assert.equal(normalizeSeedSettings({ seedMode: "fixed", fixedSeed: "" }).fixedSeed, null);
  assert.equal(normalizeSeedSettings({ fixedSeed: SEED_MAX + 1 }).fixedSeed, null);
  assert.equal(resolveSeed({ seedMode: "fixed", fixedSeed: 0 }), 0);
  assert.throws(() => resolveSeed({ seedMode: "fixed", fixedSeed: null }), RangeError);
});

test("secure random seeds use browser crypto and stay in signed 32-bit range", () => {
  const cryptoApi = { getRandomValues: values => { values[0] = 0xffffffff; return values; } };
  assert.equal(secureRandomSeed(cryptoApi), SEED_MAX);
});

test("random generation resolves independent seeds while fixed remains fixed", () => {
  let value = 10;
  const random = () => value++;
  assert.deepEqual(resolveGenerationSeeds({ seedMode: "random" }, 3, random), [10, 11, 12]);
  assert.deepEqual(resolveGenerationSeeds({ seedMode: "fixed", fixedSeed: 3407 }, 3, random), [3407, 3407, 3407]);
  let repeated = 5;
  assert.deepEqual(resolveGenerationSeeds({ seedMode: "random" }, 2, () => repeated++ < 6 ? 5 : 6), [5, 6]);
});

test("one snapshot freezes seeds, while regenerate and retry resolve fresh random values", () => {
  let value = 100;
  const random = () => value++;
  const request = { workspace: { variations: 2, creativeRequest: "same" }, llm: { seedMode: "random" } };
  const first = snapshotGenerationRequest(request, random);
  const second = snapshotGenerationRequest(request, random);
  assert.deepEqual(first.resolvedSeeds, [100, 101]);
  assert.deepEqual(second.resolvedSeeds, [102, 103]);
  assert.equal(first.workspace.creativeRequest, second.workspace.creativeRequest);
  assert.equal(resolveSeed({ seedMode: "random" }, random), 104);
  assert.equal(resolveSeed({ seedMode: "random" }, random), 105);
});
