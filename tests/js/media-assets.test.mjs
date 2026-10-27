import assert from "node:assert/strict";
import test from "node:test";
import { readMediaReference, mediaPayloadStatus, metadataOnlyWorkspace, adoptSavedMedia } from "../../web/js/media-assets.js";
import { api } from "../../web/js/api.js";

for (const kind of ["audio", "video"]) {
  test(`${kind}: actual File is binary request body; state holds only descriptor and metadata`, async () => {
    const original = globalThis.fetch;
    const file = new File([new Uint8Array([0, 1, 255, 8])], `reference.${kind}`, { type: `${kind}/test` });
    const descriptor = { sha256: "a".repeat(64), byteLength: 4, uploadId: "b".repeat(32) };
    globalThis.fetch = async (url, options) => {
      assert.equal(url, `/api/media/${kind}`);
      assert.equal(options.body, file);
      assert.equal(options.headers["Content-Type"], `${kind}/test`);
      assert.deepEqual([...new Uint8Array(await options.body.arrayBuffer())], [0, 1, 255, 8]);
      return { ok: true, json: async () => descriptor };
    };
    try {
      const asset = await readMediaReference(file, kind, async f => { assert.equal(f, file); return 1.25; });
      assert.deepEqual(asset, { filename: file.name, mimeType: file.type, durationSeconds: 1.25, mediaPayload: descriptor });
      assert.match(mediaPayloadStatus(asset), /save Project/);
      const restored = structuredClone({ ...asset, mediaPayload: { ...descriptor, uploadId: null, projectId: "saved" } });
      assert.equal(mediaPayloadStatus(restored), "File payload saved");
      assert.ok(JSON.stringify(asset).length < 400);
    } finally { globalThis.fetch = original; }
  });

  test(`${kind}: oversize rejected before upload; backend failure remains useful`, async () => {
    const original = globalThis.fetch;
    globalThis.fetch = () => { throw new Error("should not upload"); };
    try {
      await assert.rejects(api.uploadMedia({ size: 1024 ** 3 }, kind), /MiB limit/);
      globalThis.fetch = async () => ({ ok: false, json: async () => ({ detail: "File is empty" }) });
      await assert.rejects(readMediaReference(new File([], "empty", { type: `${kind}/test` }), kind,
        () => { throw new Error("duration should not run"); }), /File is empty/);
    } finally { globalThis.fetch = original; }
  });

  test(`${kind}: Workspace export/import removes authority without mutating saved state`, () => {
    const workspace = { assets: [{ kind, attached: true, filename: "clip", mediaPayload: { projectId: "saved", sha256: "a" } },
      { kind: "image", image: { data_url: "data:image/png;base64,AA==" } }] };
    const exported = metadataOnlyWorkspace(workspace);
    assert.ok(!("mediaPayload" in exported.assets[0]));
    assert.equal(exported.assets[0].attached, false);
    assert.equal(workspace.assets[0].mediaPayload.projectId, "saved");
    assert.deepEqual(exported.assets[1], workspace.assets[1]);
    assert.match(mediaPayloadStatus(exported.assets[0]), /unavailable.*reattach/);
    assert.deepEqual(metadataOnlyWorkspace(exported), exported);
  });
}


test("Save replaces upload authority but preserves concurrent reattachment and filename edits", () => {
  const assets = [{ id: "a", filename: "renamed", mediaPayload: { sha256: "same", byteLength: 4, uploadId: "tmp" } },
    { id: "b", mediaPayload: { sha256: "changed", byteLength: 5, uploadId: "new" } }];
  const workspace = { assets: [{ id: "a", filename: "old", mediaPayload: { sha256: "same", byteLength: 4, projectId: "saved" } },
    { id: "b", mediaPayload: { sha256: "old", byteLength: 4, projectId: "saved" } }] };
  adoptSavedMedia(assets, workspace);
  assert.equal(assets[0].filename, "renamed");
  assert.equal(assets[0].mediaPayload.projectId, "saved");
  assert.equal(assets[1].mediaPayload.uploadId, "new");
});


test("canonical unavailable state clears stale pixels and descriptors but preserves concurrent replacements", async () => {
  const { pictureDigest } = await import("../../web/js/reference-authority.js");
  const url = `data:image/png;base64,${Buffer.from([1, 2, 3]).toString("base64")}`;
  const digest = await pictureDigest(url);
  const assets = [{ id: "picture", kind: "image", image: { data_url: url } },
    { id: "audio", kind: "audio", mediaPayload: { sha256: "old", projectId: "saved" } },
    { id: "new", kind: "video", mediaPayload: { sha256: "new", uploadId: "upload" } },
    { id: "exact", kind: "audio", mediaPayload: { sha256: "old", uploadId: "exact-upload" } }];
  await adoptSavedMedia(assets, { assets: [
    { id: "picture", payloadAvailability: "missing", payloadSha256: digest },
    { id: "audio", payloadAvailability: "corrupt", payloadSha256: "old" },
    { id: "new", payloadAvailability: "missing", payloadSha256: "old" },
    { id: "exact", payloadAvailability: "missing", payloadSha256: "old" },
  ] });
  assert.equal(assets[0].image, null);
  assert.equal(assets[1].mediaPayload, null);
  assert.equal(assets[2].mediaPayload.sha256, "new");
  assert.equal(assets[3].mediaPayload.uploadId, "exact-upload");
  assert.match(mediaPayloadStatus(assets[0]), /missing.*replace Picture/);
  assert.match(mediaPayloadStatus(assets[1]), /corrupt.*reattach/);
});
