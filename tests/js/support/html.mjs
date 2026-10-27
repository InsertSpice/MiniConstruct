// Minimal HTML mount for structural regressions, using the suite's DOM elements.
export function mountHtml(html, doc) {
  const root = doc.createElement("document");
  const stack = [root];
  const voids = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"]);
  for (const token of html.match(/<!--[\s\S]*?-->|<[^>]+>|[^<]+/g) || []) {
    if (token.startsWith("<!")) continue;
    if (token.startsWith("</")) { stack.pop(); continue; }
    if (!token.startsWith("<")) continue;
    const tag = token.match(/^<([\w-]+)/)?.[1]; if (!tag) continue;
    const node = doc.createElement(tag);
    for (const [, key, value] of token.matchAll(/([\w-]+)="([^"]*)"/g)) {
      node.setAttribute(key, value);
      if (key === "id") node.id = value;
      if (key === "class") node.className = value;
    }
    stack.at(-1).append(node);
    if (!voids.has(tag)) stack.push(node);
  }
  return root;
}
export function domOrder(root) {
  return root.children.flatMap(child => [child, ...domOrder(child)]);
}
