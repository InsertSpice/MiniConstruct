import assert from "node:assert/strict";
import test from "node:test";

import { resolveProjectSave } from "../../web/js/project-save.js";

const projects = [{ id: "a", name: "Classroom Test" }, { id: "other", name: "Existing Other Project" }];

test("unchanged loaded name updates in place", () => {
  assert.deepEqual(resolveProjectSave({ currentProjectId: "a", currentProjectSavedName: "Classroom Test", currentProjectName: "Classroom Test", projects }), { create: false, collision: false });
  assert.deepEqual(resolveProjectSave({ currentProjectId: "a", currentProjectSavedName: "Classroom Test", currentProjectName: " classroom test ", projects }), { create: false, collision: false });
});

test("renamed loaded project forks while collisions are rejected", () => {
  assert.deepEqual(resolveProjectSave({ currentProjectId: "a", currentProjectSavedName: "Classroom Test", currentProjectName: "HeightTest", projects }), { create: true, collision: false });
  assert.deepEqual(resolveProjectSave({ currentProjectId: "a", currentProjectSavedName: "Classroom Test", currentProjectName: "Existing Other Project", projects }), { create: true, collision: true });
});

test("new workspaces and Save As create records", () => {
  assert.deepEqual(resolveProjectSave({ currentProjectId: null, currentProjectSavedName: null, currentProjectName: "HeightTest", projects }), { create: true, collision: false });
  assert.deepEqual(resolveProjectSave({ currentProjectId: "new", currentProjectSavedName: "HeightTest", currentProjectName: "HeightTest", projects, asNew: true }), { create: true, collision: false });
});

test("explicit Rename updates the loaded record while still rejecting another project's name", () => {
  assert.deepEqual(resolveProjectSave({ currentProjectId: "a", currentProjectSavedName: "Classroom Test", currentProjectName: "HeightTest", projects, renameInPlace: true }), { create: false, collision: false });
  assert.deepEqual(resolveProjectSave({ currentProjectId: "a", currentProjectSavedName: "Classroom Test", currentProjectName: "Existing Other Project", projects, renameInPlace: true }), { create: false, collision: true });
});
