import { api } from "./api.js";

export async function readMediaReference(file, kind, readDuration) {
  const mediaPayload = await api.uploadMedia(file, kind);
  const durationSeconds = await readDuration(file, kind);
  return { filename: file.name, mimeType: file.type || `${kind}/unknown`, durationSeconds, mediaPayload };
}

export function mediaPayloadStatus(asset) {
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
export function adoptSavedMedia(assets, workspace) {
  const saved = new Map((workspace?.assets || []).map(asset => [asset.id, asset]));
  for (const asset of assets) {
    const payload = saved.get(asset.id)?.mediaPayload;
    if (payload && asset.mediaPayload?.sha256 === payload.sha256 && asset.mediaPayload.byteLength === payload.byteLength) {
      asset.mediaPayload = structuredClone(payload);
    }
  }
}
