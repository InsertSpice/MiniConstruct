// Explicit copy/reset choices; defaults are re-established for every dialog.
export function createProjectGeneratedState({ root, getContext, saveAs, clearGeneratedState, notify }) {
  const find = id => root.querySelector(`#${id}`);
  const saveDialog = find("project-save-as-dialog"), clearDialog = find("project-clear-dialog");
  const mode = find("project-copy-mode"), keep = find("project-keep-prompts");
  const syncMode = () => { find("project-keep-prompts-row").hidden = mode.value !== "fresh"; };
  mode.addEventListener("change", syncMode);
  find("project-save-as").addEventListener("click", () => {
    find("project-copy-name").value = getContext().name;
    mode.value = "full"; keep.checked = false; syncMode(); saveDialog.showModal();
  });
  find("project-clear-generated").addEventListener("click", () => {
    const c = getContext();
    if (!c.saved) { notify("Open a filesystem Project before clearing generated state."); return; }
    if (c.dirty) { notify("Save or discard authoring edits before clearing generated state."); return; }
    find("project-clear-prompts").checked = false; clearDialog.showModal();
  });
  find("project-copy-confirm").addEventListener("click", async () => {
    const name = find("project-copy-name").value.trim();
    if (!name) { notify("Enter a Project name."); return; }
    const button = find("project-copy-confirm"); button.disabled = true;
    try {
      const result = await saveAs({ name, copyMode: mode.value, keepSavedPrompts: mode.value === "fresh" && keep.checked });
      if (result?.saved) saveDialog.close();
    } finally { button.disabled = false; }
  });
  find("project-clear-confirm").addEventListener("click", async () => {
    const button = find("project-clear-confirm"); button.disabled = true;
    try { if (await clearGeneratedState(find("project-clear-prompts").checked)) clearDialog.close(); }
    finally { button.disabled = false; }
  });
  find("project-copy-cancel").addEventListener("click", () => saveDialog.close());
  find("project-clear-cancel").addEventListener("click", () => clearDialog.close());
}
