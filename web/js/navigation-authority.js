// One authority for asynchronous operations that can adopt Project context.
export function createNavigationAuthority() {
  let sequence = 0;
  return {
    capture(projectId, generationId) {
      return { id: sequence, projectId, generationId };
    },
    begin(projectId, generationId) {
      return { id: ++sequence, projectId, generationId };
    },
    current(ticket) { return Boolean(ticket && ticket.id === sequence); },
    ownsContext(ticket, projectId, generationId = undefined) {
      return this.current(ticket) && ticket.projectId === projectId
        && (generationId === undefined || ticket.generationId === generationId);
    },
    invalidate() { sequence += 1; },
  };
}
