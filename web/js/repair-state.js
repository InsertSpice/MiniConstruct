export function canStartRepair(repairRunning) {
  return !repairRunning;
}

export function repairUiState({ hasOutput, generating, repairRunning }) {
  return {
    disabled: !hasOutput || generating || repairRunning,
    label: repairRunning ? "Repairing…" : "Repair Format",
    busy: repairRunning,
  };
}
