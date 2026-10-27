"""One metadata policy for filtered Fresh forks and in-place generated-state reset.

Only canonical authoring files are reachable after this projection. Disposable
Take evidence/handoff caches are owned by the removed Takes and use TakeStore GC.
Preparation receipts are process-local/browser-local, not persisted here.
"""
from dataclasses import dataclass

from miniconstruct.execution_state import ProjectExecutionState


@dataclass(frozen=True)
class GeneratedStateReset:
    keep_saved_prompts: bool = True

    def project(self, generations, execution_state):
        generations = [generation.model_copy(deep=True, update={
            "takes": [], "selected_take_id": None, "take_observations": [],
            "render_submissions": [],
        }) for generation in generations]
        # Configuration survives; exact selection maps and native lineage do not.
        state = ProjectExecutionState(
            savedPrompts=execution_state.saved_prompts if self.keep_saved_prompts else {},
            routing=execution_state.routing,
            rendererSeedSettings=execution_state.renderer_seed_settings,
            workflowControlDefaults=execution_state.workflow_control_defaults,
            workflowControlOverrides=execution_state.workflow_control_overrides,
            continuationSettings={key: settings.model_copy(update={"source_out_frame": None})
                                  for key, settings in execution_state.continuation_settings.items()},
        ).model_copy(deep=True)
        return generations, state
