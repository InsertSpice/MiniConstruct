export function canEjectModel({ modelId, generating, revising, repairing, ejecting }) {
  return Boolean(modelId?.trim()) && !generating && !revising && !repairing && !ejecting;
}
