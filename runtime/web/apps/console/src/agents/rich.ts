// A step's body is a small HTML subset, parsed here into a tree that React
// renders as elements. Nothing reaches innerHTML, and anything outside the
// subset (attributes, other tags, broken nesting) fails the parse, which drops
// the agent; content.test.ts turns that into a failing test.

export type Tag = "p" | "ul" | "ol" | "li" | "strong" | "em" | "b" | "i" | "code" | "kbd" | "br";
export type Rich = string | { tag: Tag; children: Rich[] };

const TAGS = new Set<string>(["p", "ul", "ol", "li", "strong", "em", "b", "i", "code", "kbd", "br"]);
const ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };
const TOKEN = /<(\/?)([a-zA-Z][a-zA-Z0-9]*)([^>]*)>|&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z]+);|[^<&]+|[<&]/g;

function entity(name: string): string | null {
  if (name.startsWith("#x")) return String.fromCodePoint(parseInt(name.slice(2), 16));
  if (name.startsWith("#")) return String.fromCodePoint(parseInt(name.slice(1), 10));
  return ENTITIES[name] ?? null;
}

export function parseRich(html: string): Rich[] | null {
  const root: Rich[] = [];
  const stack: { tag: Tag; children: Rich[] }[] = [];
  const children = () => (stack.length ? stack[stack.length - 1].children : root);
  const text = (value: string) => {
    const list = children();
    const last = list[list.length - 1];
    if (typeof last === "string") list[list.length - 1] = last + value;
    else list.push(value);
  };

  for (const [token, closing, name, rest, ent] of html.matchAll(TOKEN)) {
    if (name !== undefined) {
      const tag = name.toLowerCase();
      const selfClosing = rest.trim() === "/";
      if (!TAGS.has(tag) || (rest.trim() !== "" && !(selfClosing && tag === "br"))) return null;
      if (tag === "br") {
        if (closing) return null;
        children().push({ tag: "br", children: [] });
      } else if (closing) {
        if (stack.pop()?.tag !== tag) return null;
      } else {
        const node = { tag: tag as Tag, children: [] };
        children().push(node);
        stack.push(node);
      }
    } else if (ent !== undefined) {
      const value = entity(ent);
      if (value === null) return null;
      text(value);
    } else {
      // A lone "<" or "&" is kept as text, so "Q&A" still reads.
      text(token);
    }
  }
  return stack.length === 0 ? root : null;
}
