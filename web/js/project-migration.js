export function prepareLegacyWorkspace(record) {
  if (!record?.workspace || typeof record.workspace !== "object") {
    throw new Error("Legacy Project does not contain a Workspace.");
  }
  const workspace = structuredClone(record.workspace);
  workspace.projectId = null;
  if (record.name) workspace.projectName = record.name;
  return workspace;
}

export async function migrateLegacyProject(record, createProject) {
  const workspace = prepareLegacyWorkspace(record);
  const created = await createProject(workspace);
  return { created, workspace };
}
