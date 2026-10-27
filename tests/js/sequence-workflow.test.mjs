import assert from "node:assert/strict";
import test from "node:test";

import {
  ephemeralSequence, generationLabel, generationOrdinal, movedGenerationIds, sequenceFromProject,
  shouldMarkWorkspaceDirty, structuralOperationAllowed,
} from "../../web/js/sequence-workflow.js";

const project = {
  sequence: {
    activeGenerationId: "b",
    generations: [
      { generationId: "a", createdAt: "1", updatedAt: "1" },
      { generationId: "b", createdAt: "2", updatedAt: "2" },
      { generationId: "c", createdAt: "3", updatedAt: "3" },
    ],
  },
};

test("Sequence summary derives human ordinals without changing stable IDs", () => {
  const sequence = sequenceFromProject(project);
  assert.equal(generationOrdinal(sequence, "b"), 2);
  assert.equal(generationLabel(sequence, "b"), "Generation 2");
  assert.deepEqual(sequence.generations.map(item => item.generationId), ["a", "b", "c"]);
});

test("move helpers swap only the active Generation", () => {
  assert.deepEqual(movedGenerationIds(project.sequence, "earlier"), ["b", "a", "c"]);
  assert.deepEqual(movedGenerationIds(project.sequence, "later"), ["a", "c", "b"]);
});

test("ephemeral and dirty states keep structural controls unavailable", () => {
  assert.equal(ephemeralSequence().generations.length, 1);
  assert.equal(structuralOperationAllowed({ saved: false, dirty: false }), false);
  assert.equal(structuralOperationAllowed({ saved: true, dirty: true }), false);
  assert.equal(structuralOperationAllowed({ saved: true, dirty: false }), true);
});

test("dirty classification excludes Sequence controls but preserves authoring controls", () => {
  const sequenceControl = { closest: selector => selector === ".left-rail" ? {} : selector === "#sequence-panel" ? {} : null };
  const storyDraftControl = { closest: selector => selector === ".left-rail" ? {} : selector === "#story-planner" ? {} : null };
  const authoringControl = { closest: selector => selector === ".left-rail" ? {} : null };
  assert.equal(shouldMarkWorkspaceDirty(sequenceControl), false);
  assert.equal(shouldMarkWorkspaceDirty(storyDraftControl), false);
  assert.equal(shouldMarkWorkspaceDirty(authoringControl), true);
  assert.equal(shouldMarkWorkspaceDirty({}), false);
});
