export function normalizedProjectName(name) {
  return (name || "Untitled Project").trim().toLocaleLowerCase();
}

export function resolveProjectSave({ currentProjectId, currentProjectSavedName, currentProjectName, projects, asNew = false, renameInPlace = false }) {
  const create = !renameInPlace && (asNew || !currentProjectId || normalizedProjectName(currentProjectSavedName) !== normalizedProjectName(currentProjectName));
  const collision = (create || renameInPlace) && projects.some(project =>
    project.id !== currentProjectId && normalizedProjectName(project.name) === normalizedProjectName(currentProjectName),
  );
  return { create, collision };
}
