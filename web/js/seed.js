export const SEED_MAX = 2147483647;

export function normalizeSeedSettings(value = {}) {
  const seedMode = ["backend_default", "random", "fixed"].includes(value.seedMode)
    ? value.seedMode : "backend_default";
  const fixedSeed = value.fixedSeed == null || (typeof value.fixedSeed === "string" && !value.fixedSeed.trim())
    ? Number.NaN : Number(value.fixedSeed);
  return {
    seedMode,
    fixedSeed: Number.isInteger(fixedSeed) && fixedSeed >= 0 && fixedSeed <= SEED_MAX ? fixedSeed : null,
  };
}

export function secureRandomSeed(cryptoApi = globalThis.crypto) {
  if (!cryptoApi?.getRandomValues) throw new Error("Secure random seed generation is unavailable in this browser.");
  const value = new Uint32Array(1);
  cryptoApi.getRandomValues(value);
  return value[0] % (SEED_MAX + 1);
}

export function resolveSeed(value, random = secureRandomSeed) {
  const settings = normalizeSeedSettings(value);
  if (settings.seedMode === "backend_default") return null;
  if (settings.seedMode === "random") return random();
  if (settings.fixedSeed === null) throw new RangeError(`Fixed seed must be an integer from 0 to ${SEED_MAX}.`);
  return settings.fixedSeed;
}

export function resolveGenerationSeeds(llm, variations, random = secureRandomSeed) {
  if (normalizeSeedSettings(llm).seedMode === "random") {
    const seeds = new Set();
    let attempts = 0;
    while (seeds.size < variations && attempts < variations * 20) {
      const seed = random();
      if (Number.isInteger(seed) && seed >= 0 && seed <= SEED_MAX) seeds.add(seed);
      attempts += 1;
    }
    if (seeds.size !== variations) throw new Error("Could not resolve distinct random seeds for this generation.");
    return [...seeds];
  }
  return Array.from({ length: variations }, () => resolveSeed(llm, random));
}

export function snapshotGenerationRequest(request, random = secureRandomSeed) {
  const snapshot = structuredClone(request);
  const variations = snapshot.workspace?.variations;
  if (Number.isInteger(variations) && variations > 0) {
    snapshot.resolvedSeeds = resolveGenerationSeeds(snapshot.llm || {}, variations, random);
  }
  return snapshot;
}
