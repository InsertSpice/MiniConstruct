// This collection is the narrow saved execution boundary. Main Prompt and other
// Workspace fields are deliberately absent. Content digests never use filenames.
export async function pictureDigest(dataUrl) {
  const bytes = Uint8Array.from(atob(dataUrl.split(",")[1]), c => c.charCodeAt(0));
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
    .map(byte => byte.toString(16).padStart(2, "0")).join("");
}

export function replacePicture(asset, file, image) {
  return { ...asset, filename: file.name, mimeType: image.data_url.slice(5).split(";")[0], image, ...(asset.payloadAvailability ? { payloadAvailability: null, payloadSha256: null } : {}) };
}

export function createReferenceAuthority({ api, getContext, getReferences, onMutation }) {
  let pending = Promise.resolve();
  const canonical = new Map();
  const sameContext = (before, now) => before.projectId === now.projectId && before.generationId === now.generationId
    && before.navigationTicket?.id === now.navigationTicket?.id;
  async function synchronize() {
    const c = { ...getContext() };
    if (!c.saved) return;
    const key = JSON.stringify([c.projectId, c.generationId, c.navigationTicket?.id]);
    const references = structuredClone(getReferences());
    const assets = references.assets;
    const digests = new Map();
    const identity = structuredClone(references);
    for (const [index, asset] of assets.entries()) {
      if (asset.kind !== "image") continue;
      const digest = asset.image?.data_url ? await pictureDigest(asset.image.data_url) : asset.payloadSha256;
      digests.set(asset.id, digest);
      if (identity.assets[index].image) delete identity.assets[index].image.data_url;
      identity.assets[index].imageSha256 = digest;
      if (!asset.image?.data_url || canonical.get(key)?.digests.get(asset.id) === digest) {
        if (asset.image) delete asset.image.data_url;
        asset.imageSha256 = digest;
      }
    }
    const latest = getContext();
    if (!sameContext(c, latest))
      throw new Error("The active Generation changed while saving Reference Assets. Reopen it to review the saved result.");
    if (c.projectEditFingerprint && latest.projectEditFingerprint !== c.projectEditFingerprint)
      throw new Error("Project edits changed while saving Reference Assets. Review the current Project before preparing.");
    const signature = JSON.stringify(identity);
    const known = canonical.get(key);
    const revision = latest.projectEditFingerprint || latest.projectFingerprint;
    if (known?.signature === signature && known.revision === revision) return;
    // Hashing Pictures can yield while a known same-tab mutation is adopted.
    // Use that active receipt before the request; never fetch/retry on conflict.
    const response = await api.syncReferenceAssets(c.projectId, c.generationId, {
      assets, subjects: references.subjects, expectedProjectFingerprint: latest.projectFingerprint,
      ...(latest.projectEditFingerprint ? { expectedProjectEditFingerprint: latest.projectEditFingerprint } : {}),
    });
    const now = getContext();
    if (!sameContext(c, now))
      throw new Error("The active Generation changed while saving Reference Assets. Reopen it to review the saved result.");
    await onMutation(response, c);
    if (!sameContext(c, getContext()))
      throw new Error("The active Generation changed while saving Reference Assets. Reopen it to review the saved result.");
    canonical.clear();
    // Remember only the snapshot actually verified by this request. Edits made
    // during hashing or transport will differ on the next synchronization.
    canonical.set(key, { digests, signature,
      revision: response.projectEditFingerprint || response.projectFingerprint || revision });
    return response;
  }
  return {
    sync() {
      const requested = { ...getContext() };
      const result = pending.then(() => {
        if (!sameContext(requested, getContext()))
          throw new Error("The active Generation changed while saving Reference Assets. Reopen it to review the saved result.");
        return synchronize();
      });
      pending = result.catch(() => {});
      return result;
    },
  };
}
