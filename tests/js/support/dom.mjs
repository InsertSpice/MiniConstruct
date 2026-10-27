export class Element {
  constructor(tag, doc) {
    this.tagName = tag; this.ownerDocument = doc; this.children = []; this.events = {}; this.attributes = {}; this.dataset = {};
    this.value = ""; this.checked = false; this.disabled = false;
  }
  append(...children) { for (const child of children) { child.parentElement = this; this.children.push(child); } }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || "") + this.children.map(child => child.textContent).join(""); }
  set innerHTML(_) { throw new Error("Unsafe HTML rendering"); }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(name, handler) { (this.events[name] ||= []).push(handler); }
  async emit(name) { for (const handler of this.events[name] || []) await handler({ target: this }); }
  matches(selector) {
    if (selector.startsWith("#")) return this.id === selector.slice(1);
    if (selector.startsWith(".")) return (this.className || "").split(" ").includes(selector.slice(1));
    return this.tagName === selector;
  }
  querySelectorAll(selectors) {
    const matches = [];
    for (const child of this.children) {
      if (selectors.split(",").some(selector => child.matches(selector.trim()))) matches.push(child);
      matches.push(...child.querySelectorAll(selectors));
    }
    return matches;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector); }
}
