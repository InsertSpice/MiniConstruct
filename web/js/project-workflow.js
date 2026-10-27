export function freshUnsavedWorkspace() {
  return {
    schemaVersion: 1, projectId: null, projectName: "Untitled Project", mode: "T2VA", durationSeconds: 6,
    shots: null, aspectRatio: "4:3", variations: 1, creativeRequest: "", dialogue: "", referenceLabels: "",
    assets: [], subjects: [], nextSubjectNumber: 1,
  };
}

// The URL remembers only which saved Project to open, never its authoring data.
export function projectIdFromLocation(href) {
  return new URL(href).searchParams.get("project");
}

export function projectLocation(href, projectId) {
  const url = new URL(href);
  if (projectId) url.searchParams.set("project", projectId);
  else url.searchParams.delete("project");
  return url.href;
}

export function freshUnsavedContinuity() {
  return { sequenceContext: "", incomingState: "", intendedOutgoingState: "" };
}

function canonicalStoryMemory(value) {
  if (!value || typeof value !== "object") return value;
  const memory = structuredClone(value);
  for (const key of ["durableFacts", "openThreads"]) {
    if (Array.isArray(memory[key])) memory[key] = memory[key].filter(entry => entry && typeof entry.text === "string" && entry.text.trim());
  }
  return memory;
}

export function continuityFromProject(project) {
  const continuity = project?.continuity || {};
  return {
    sequenceContext: continuity.sequenceContext || "",
    incomingState: continuity.incomingState || "",
    intendedOutgoingState: continuity.intendedOutgoingState || "",
  };
}

export function resetUnsavedProjectState(state) {
  state.activeProjectType = "none";
  state.currentProjectId = null;
  state.currentProjectSavedName = null;
  state.currentLegacyProjectId = null;
  return state;
}

export function prepareImportedWorkspace(workspace) {
  const imported = structuredClone(workspace);
  imported.projectId = null;
  return imported;
}

// Distinguish an absent legacy cast from an explicitly empty canonical cast.
// The raw envelope preserves field presence while validation supplies defaults.
export function importedSequenceMetadata(payload, validated) {
  const hasCanonicalMetadata = Array.isArray(payload?.sequenceSubjects)
    || validated?.sequenceNextSubjectNumber != null;
  if (!hasCanonicalMetadata) return { sequenceSubjects: null, sequenceNextSubjectNumber: null };
  return {
    sequenceSubjects: Array.isArray(validated?.sequenceSubjects) ? validated.sequenceSubjects : [],
    sequenceNextSubjectNumber: validated?.sequenceNextSubjectNumber ?? null,
  };
}

export function clearImportedSequenceMetadata(state) {
  state.importedSequenceSubjects = null;
  state.importedSequenceNextSubjectNumber = null;
  return state;
}

export async function loadProjectSources({ listFilesystem, listLegacy, cachedFilesystem = [], cachedLegacy = [] }) {
  const [filesystemResult, legacyResult] = await Promise.allSettled([listFilesystem(), listLegacy()]);
  return {
    filesystemProjects: filesystemResult.status === "fulfilled" ? (filesystemResult.value.projects || []) : cachedFilesystem,
    legacyProjects: legacyResult.status === "fulfilled" ? legacyResult.value : cachedLegacy,
    filesystemOk: filesystemResult.status === "fulfilled",
    legacyOk: legacyResult.status === "fulfilled",
    filesystemError: filesystemResult.status === "rejected" ? filesystemResult.reason : null,
    legacyError: legacyResult.status === "rejected" ? legacyResult.reason : null,
  };
}

export async function persistFilesystemProject({ projectId, workspace, continuity, planning, storyIdea, storyCreativeDirection, storyPlanningStyle, storyMemory, memoryAuthority, sequenceSubjects, sequenceNextSubjectNumber, characterProfiles, dialogueLanguage, createProject, saveProject }) {
  storyMemory = canonicalStoryMemory(storyMemory);
  const response = projectId
    ? await saveProject(projectId, workspace, continuity, planning, storyIdea, storyMemory, memoryAuthority, storyCreativeDirection, characterProfiles, dialogueLanguage, storyPlanningStyle)
    : await createProject(workspace, continuity, planning, storyIdea, storyMemory, storyCreativeDirection, sequenceSubjects, characterProfiles, sequenceNextSubjectNumber, dialogueLanguage, storyPlanningStyle);
  return { projectId: response.id, name: response.name, workspace: response.workspace, sequence: response.sequence, response };
}

export async function openFilesystemProjectSafely({ id, currentWorkspace, loadProject }) {
  try {
    return { ok: true, project: await loadProject(id) };
  } catch (error) {
    return { ok: false, project: null, workspace: currentWorkspace, error };
  }
}

export async function openLegacyProjectSafely({ id, currentWorkspace, loadProject }) {
  try {
    const project = await loadProject(id);
    if (!project) throw new Error("Legacy Project was not found in this browser.");
    return { ok: true, project };
  } catch (error) {
    return { ok: false, project: null, workspace: currentWorkspace, error };
  }
}

export async function deleteFilesystemProjectAndReset({ id, deleteProject, filesystemProjects, reset }) {
  await deleteProject(id);
  const deletedIndex = filesystemProjects.findIndex(project => project.id === id);
  if (deletedIndex >= 0) filesystemProjects.splice(deletedIndex, 1);
  return reset();
}

export async function deleteLegacyProjectAndReset({ id, deleteProject, legacyProjects, reset }) {
  await deleteProject(id);
  if (legacyProjects) {
    const deletedIndex = legacyProjects.findIndex(project => project.id === id);
    if (deletedIndex >= 0) legacyProjects.splice(deletedIndex, 1);
  }
  return reset();
}
