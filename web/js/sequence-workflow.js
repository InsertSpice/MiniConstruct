export const EPHEMERAL_GENERATION_ID = "ephemeral-generation";

export function shouldMarkWorkspaceDirty(target) {
  return Boolean(target?.closest?.(".left-rail") && !target.closest("#sequence-panel"));
}

export function ephemeralSequence() {
  return { activeGenerationId: null, generations: [{ generationId: null, createdAt: null, updatedAt: null }] };
}

export function sequenceFromProject(project) {
  if (!project?.sequence?.generations?.length) return ephemeralSequence();
  return {
    activeGenerationId: project.sequence.activeGenerationId,
    generations: project.sequence.generations.map(generation => ({ ...generation })),
  };
}

export function generationOrdinal(sequence, generationId) {
  const index = (sequence?.generations || []).findIndex(item => item.generationId === generationId);
  return index < 0 ? null : index + 1;
}

export function generationLabel(sequence, generationId) {
  const ordinal = generationOrdinal(sequence, generationId);
  return ordinal == null ? "Generation 1" : `Generation ${ordinal}`;
}

export function movedGenerationIds(sequence, direction) {
  const ids = (sequence?.generations || []).map(item => item.generationId);
  const activeIndex = ids.indexOf(sequence?.activeGenerationId);
  const targetIndex = activeIndex + (direction === "earlier" ? -1 : 1);
  if (activeIndex < 0 || targetIndex < 0 || targetIndex >= ids.length) return ids;
  [ids[activeIndex], ids[targetIndex]] = [ids[targetIndex], ids[activeIndex]];
  return ids;
}

export function structuralOperationAllowed({ saved, dirty }) {
  return Boolean(saved && !dirty);
}
