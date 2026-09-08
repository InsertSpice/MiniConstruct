import { normalizeCreativeControls } from "./creative-controls.js";
import { normalizeCreativeInterpretation } from "./creative-interpretation.js";


export function captureWorkspaceCreativeState(state) {
  return {
    creativeInterpretation: normalizeCreativeInterpretation(state.creativeInterpretation),
    creativeControls: structuredClone(normalizeCreativeControls(state.creativeControls)),
  };
}


export function restoreWorkspaceCreativeState(workspace) {
  return {
    creativeInterpretation: normalizeCreativeInterpretation(workspace.creativeInterpretation),
    creativeControls: normalizeCreativeControls(workspace.creativeControls),
  };
}
