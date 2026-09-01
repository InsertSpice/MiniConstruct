async function request(path, body) {
  const response = await fetch(`/api/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map(item => `${(item.loc || []).slice(1).join(".")}: ${item.msg}`).join("; ")
      : data.detail || `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return data;
}

async function streamRequest(path, body, { signal, onEvent }) {
  const response = await fetch(`/api/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    let data = {};
    try { data = await response.json(); } catch { /* response was not JSON */ }
    const detail = Array.isArray(data.detail)
      ? data.detail.map(item => `${(item.loc || []).slice(1).join(".")}: ${item.msg}`).join("; ")
      : data.detail || `Request failed (${response.status})`;
    throw new Error(detail);
  }
  if (!response.body) throw new Error("This browser did not expose the generation stream.");
  const { createEventStreamParser } = await import("./streaming.js");
  const parser = createEventStreamParser(onEvent);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
    }
    parser.push(decoder.decode());
    parser.finish();
  } finally {
    reader.releaseLock();
  }
}

export const api = {
  models: settings => request("models", settings),
  discoverEndpoints: body => request("discover-endpoints", body),
  test: settings => request("test-connection", settings),
  eject: settings => request("model-management/eject", settings),
  assemble: body => request("assemble", body),
  generate: body => request("generate", body),
  streamGenerate: (body, options) => streamRequest("generate-stream", body, options),
  streamRevision: (body, options) => streamRequest("revise-stream", body, options),
  validate: body => request("validate", body),
  repair: body => request("repair", body),
  validateProject: body => request("validate-project", body),
};
