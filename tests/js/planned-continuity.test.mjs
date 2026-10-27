import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { buildGenerationRequest } from "../../web/js/generation-request.js";
import { continuityFromProject, persistFilesystemProject, openFilesystemProjectSafely } from "../../web/js/project-workflow.js";
import { planningFromProject } from "../../web/js/planning-workflow.js";

test("saved continuity edit/reload and Generation switching retain exact separate state", async () => {
  const generations = new Map();
  const workspace = { creativeRequest: "Walk to the door.", dialogue: "Hello." };
  for (const id of ["g1", "g2"]) {
    const continuity = { sequenceContext: " Blue bedroom.\n", incomingState: `${id} start\n`, intendedOutgoingState: `${id} end` };
    const planning = { relationshipToPrevious: id === "g1" ? null : "new_shot_preserve_continuity", cameraIntent: `${id} camera` };
    await persistFilesystemProject({ projectId: "project", workspace, continuity, planning,
      saveProject: async (projectId, savedWorkspace, savedContinuity, savedPlanning) => {
        const value = structuredClone({ id: projectId, workspace: savedWorkspace, continuity: savedContinuity, planning: savedPlanning,
          sequence: { activeGenerationId: id, generations: [{ generationId: "g1" }, { generationId: "g2" }] } });
        generations.set(id, value); return value;
      } });
  }
  for (const id of ["g2", "g1", "g2"]) {
    const loaded = await openFilesystemProjectSafely({ id, loadProject: async key => structuredClone(generations.get(key)) });
    assert.equal(loaded.ok, true);
    assert.equal(continuityFromProject(loaded.project).incomingState, `${id} start\n`);
    assert.equal(continuityFromProject(loaded.project).sequenceContext, " Blue bedroom.\n");
    assert.equal(planningFromProject(loaded.project).cameraIntent, `${id} camera`);
    const request = buildGenerationRequest({ workspace: loaded.project.workspace, llm: {}, filesystemProjectId: "project", generationId: id });
    assert.equal(request.generationId, id);
    assert.equal(request.workspace.creativeRequest, workspace.creativeRequest);
    assert.equal(request.continuityContext, undefined);
    assert.equal(request.incomingState, undefined);
  }
  assert.equal(buildGenerationRequest({ workspace, llm: {} }).generationId, null);
});

test("Planned Continuity help distinguishes saved H3 authority and separate Story Memory", async () => {
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  assert.match(html, /<summary>Planned Continuity /);
  assert.match(html, /Save these fields to influence H3 authoring/);
  assert.match(html, /H3 uses the saved Project values/);
  assert.match(html, /START of this Generation/);
  assert.match(html, /Desired state by the END/);
  assert.doesNotMatch(html, /They do not change H3 prompt generation yet/);
  const story = await readFile(new URL("../../web/js/story-planning.js", import.meta.url), "utf8");
  assert.match(story, /Story Memory After Generation/);
  assert.doesNotMatch(html, /id="(?:selected-take|observed-end-state)"/);
});
