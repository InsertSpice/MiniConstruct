import { pictureDigest } from "./reference-authority.js";
import { api } from "./api.js";

export async function readMediaReference(file, kind, readDuration) {
  const mediaPayload = await api.uploadMedia(file, kind);
  const durationSeconds = await readDuration(file, kind);
  return { filename: file.name, mimeType: file.type || `${kind}/unknown`, durationSeconds, mediaPayload };
}

export function mediaPayloadStatus(asset) {
  if (asset.payloadAvailability) return `File payload ${asset.payloadAvailability}: ${asset.kind === "image" ? "replace Picture" : "reattach file"} before execution`;
  return asset.mediaPayload
    ? (asset.mediaPayload.projectId ? "File payload saved" : "File ready: save Project to keep it")
    : "File payload unavailable: reattach file before execution";
}

export function metadataOnlyWorkspace(workspace) {
  const copy = structuredClone(workspace);
  for (const asset of copy.assets || []) {
    delete asset.mediaPayload;
    if (asset.kind !== "image") asset.attached = false;
  }
  return copy;
}

// Apply only authority for bytes still present; preserve edits made during Save.
export async function adoptSavedMedia(assets, workspace) {
  const saved = new Map((workspace?.assets || []).map(asset => [asset.id, asset]));
  for (const asset of assets) {
    const canonical = saved.get(asset.id);
    if (canonical?.payloadAvailability) {
      if (asset.mediaPayload?.uploadId) continue; // Explicit concurrent reattachment owns new bytes.
      const currentImage = asset.image;
      const digest = asset.kind === "image" && asset.image?.data_url
        ? await pictureDigest(asset.image.data_url) : asset.mediaPayload?.sha256 || asset.payloadSha256;
      if (digest === canonical.payloadSha256 && asset.image === currentImage) {
        asset.attached = false;
        asset.payloadAvailability = canonical.payloadAvailability;
        asset.payloadSha256 = canonical.payloadSha256;
        asset.image = null;
        asset.mediaPayload = null;
      }
      continue;
    }
    const payload = canonical?.mediaPayload;
    if (payload && asset.mediaPayload?.sha256 === payload.sha256 && asset.mediaPayload.byteLength === payload.byteLength) {
      asset.mediaPayload = structuredClone(payload);
    }
  }
}
