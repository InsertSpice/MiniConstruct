"""Read-only, one-shot Sequence render candidate inspection."""
from miniconstruct.execution import ConfigNotFound, ExecutionStore, InvalidBindings
from miniconstruct.project_store import ProjectStore
from miniconstruct.workflow_preparation import prepare_workflow


def sequence_render_readiness(project_id: str, store: ProjectStore | None = None,
                              config: ExecutionStore | None = None) -> dict:
    store = store or ProjectStore()
    config = config or ExecutionStore()
    # The manifest is the sole order and output authority. The sidecar supplies
    # explicit prompt, Profile, and native acceptance decisions.
    directory = store._project_directory(project_id, must_exist=True)
    manifest = store._read_manifest(directory)
    generations, _ = store._generations(manifest)
    sidecar = store._read_execution_state(directory)
    connections = config.list("connections")
    connection = connections[0] if len(connections) == 1 else None

    for number, generation in enumerate(generations, 1):
        gid = generation.generation_id
        label = f"Generation G{number}"
        base = {"generationId": gid, "generationNumber": number,
                "generationLabel": label, "candidate": False, "prepareAttempted": False}
        selected_take = next((take for take in generation.takes if take.id == generation.selected_take_id), None)
        accepted_id = sidecar.accepted_continuations.get(gid)
        accepted = next((record for record in sidecar.continuation_executions
                         if str(record.id) == str(accepted_id) and record.status == "available" and record.confirmed
                         and str(record.generation_id) == gid
                         and record.render_submission_id in {s.id for s in generation.render_submissions}), None)
        if selected_take or accepted:
            # An explicit output/native decision remains authoritative during a
            # later reroll. The pending reroll cannot replace it.
            continue
        statuses = {submission.job.status for submission in generation.render_submissions}
        if statuses & {"queued", "running"}:
            return {**base, "rendererStatus": "active", "reason":
                    f"{label} already has an active render. Watch, Cancel or Recover that attempt first."}
        if "unknown" in statuses:
            return {**base, "rendererStatus": "unknown", "reason":
                    f"{label} has an unresolved render attempt. Recover that exact attempt before automatically starting another."}
        completed = [submission for submission in generation.render_submissions if submission.job.status == "completed"]
        if completed:
            outputs = sum(len(submission.job.outputs) for submission in completed)
            detail = ("choose/import the intended output" if outputs else
                      "inspect the completed render and choose its output or native state")
            return {**base, "rendererStatus": "completed", "reason":
                    f"{label} completed; {detail} before automatic Sequence progression."}

        selected = sidecar.selections.get(gid)
        if selected is None:
            return {**base, "rendererStatus": "failed_or_cancelled" if statuses else "none",
                    "reason": f"{label} needs an explicit H3 execution selection. Open it and use the ordinary execution controls."}

        # Continuation status uses the existing inherited Profile preference rule.
        continuation = store.continuation_status(project_id, gid)
        profile_id = continuation["settings"].profile_id
        if profile_id is None:
            return {**base, "reason": f"{label} needs a saved Workflow Profile choice. Use this profile for continuity in execution."}
        try:
            profile = config.get("profiles", profile_id)
        except ConfigNotFound:
            return {**base, "reason": f"{label}'s saved Workflow Profile is unavailable. Choose an existing Profile."}

        result = {**base, "candidate": True, "profileId": str(profile.id), "profileName": profile.name,
                  "connectionId": str(connection.id) if connection else None,
                  "connectionName": connection.name if connection else None,
                  "connectionRequired": connection is None,
                  "promptSummary": selected.prompt[:160],
                  "rendererStatus": "failed_or_cancelled" if statuses else "none",
                  "prepareAttempted": True}
        try:
            prepared = prepare_workflow(store, project_id, gid, profile, inspection=True, read_only_handoff=True)
        except (InvalidBindings, ValueError) as exc:
            return {**result, "candidate": False, "prepareReady": False,
                    "reason": f"{label} cannot currently Prepare: {exc}"}
        native = prepared.native_continuation
        if native and native.consume:
            result["continuitySource"] = f"Accepted native state from Generation G{number - 1} ({native.source_id})"
        elif any(ref.asset_id.startswith("handoff:") for ref in prepared.reference_map):
            result["continuitySource"] = f"Selected Take handoff from Generation G{number - 1}"
        else:
            result["continuitySource"] = "None used by current Prepare"
        result.update(prepareReady=True, reason=f"{label} is the earliest unresolved render step and Prepare is ready.",
                      workflowControls=prepared.workflow_controls, rendererSeedSettings=prepared.renderer_seed_settings.model_dump(mode="json", by_alias=True),
                      warnings=prepared.warnings)
        return result
    return {"candidate": False, "prepareAttempted": False, "reason":
            "Every Generation has a completed output or accepted native state. No new render is proposed."}
