export { snapshotGenerationRequest } from "./seed.js";

export function createEventStreamParser(onEvent) {
  let buffer = "";

  function emit(block) {
    let event = "message";
    const data = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (!data.length) return;
    const raw = data.join("\n");
    let parsed;
    try { parsed = JSON.parse(raw); } catch { return; }
    onEvent({ event, data: parsed });
  }

  return {
    push(chunk) {
      buffer = (buffer + chunk).replaceAll("\r\n", "\n");
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        emit(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
      }
    },
    finish() {
      if (buffer.trim()) emit(buffer);
      buffer = "";
    },
  };
}
