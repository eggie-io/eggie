// A page's Markdown, lexed by marked and mapped to a tree of the kinds the console draws.
// marked's HTML output is never used: raw HTML in a page stays text.
import { lexer, type MarkedToken, type Token } from "marked";
import { resolveImage, resolveLink, type LinkTarget } from "./pages";

export type Inline =
  | string
  | { kind: "strong" | "em" | "del"; children: Inline[] }
  | { kind: "code"; text: string }
  | { kind: "br" }
  | { kind: "link"; target: LinkTarget; children: Inline[] }
  | { kind: "image"; path: string | null; alt: string };

type HeadingLevel = 1 | 2 | 3 | 4 | 5 | 6;
export type Align = "left" | "center" | "right" | null;

export type Block =
  | { kind: "heading"; level: HeadingLevel; children: Inline[] }
  | { kind: "paragraph"; children: Inline[] }
  | { kind: "code"; lang: string; text: string }
  | { kind: "qr"; value: string }
  | { kind: "list"; ordered: boolean; start: number; items: Block[][] }
  | { kind: "table"; align: Align[]; header: Inline[][]; rows: Inline[][][] }
  | { kind: "quote"; children: Block[] }
  | { kind: "hr" };

const ENTITIES: Record<string, string> = {
  amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: "\u00a0", mdash: "\u2014", ndash: "\u2013",
  hellip: "\u2026", copy: "\u00a9", reg: "\u00ae", trade: "\u2122", laquo: "\u00ab", raquo: "\u00bb",
  middot: "\u00b7", bull: "\u2022", rarr: "\u2192", larr: "\u2190",
};

// marked leaves character references for its HTML output to resolve; an unknown one stays as typed.
function decode(text: string): string {
  return text.replace(/&(#[0-9]{1,7}|#x[0-9a-f]{1,6}|[a-z]+);/gi, (whole, name: string) => {
    if (name[0] !== "#") return ENTITIES[name] ?? whole;
    const code = name[1] === "x" || name[1] === "X" ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10);
    return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : whole;
  });
}

const box = (checked: boolean | undefined) => (checked ? "☑ " : "☐ ");

function item(tokens: Token[]): Block[] {
  const [first, ...rest] = tokens as MarkedToken[];
  if (first?.type !== "checkbox") return blocks(tokens);
  const body = blocks(rest);
  const lead = body[0];
  if (lead?.kind === "paragraph") return [{ ...lead, children: [box(first.checked), ...lead.children] }, ...body.slice(1)];
  return [{ kind: "paragraph", children: [box(first.checked)] }, ...body];
}

function inlines(tokens: Token[] | undefined): Inline[] {
  return ((tokens ?? []) as MarkedToken[]).flatMap((t): Inline[] => {
    switch (t.type) {
      case "text":
        return t.tokens ? inlines(t.tokens) : [decode(t.text)];
      case "escape":
        return [t.text];
      case "strong":
      case "em":
      case "del":
        return [{ kind: t.type, children: inlines(t.tokens) }];
      case "codespan":
        return [{ kind: "code", text: t.text }];
      case "br":
        return [{ kind: "br" }];
      case "link":
        return [{ kind: "link", target: resolveLink(t.href), children: inlines(t.tokens) }];
      case "image":
        return [{ kind: "image", path: resolveImage(t.href), alt: t.text }];
      case "checkbox":
        return [box(t.checked)];
      default:
        return [t.raw];
    }
  });
}

function blocks(tokens: Token[] | undefined): Block[] {
  return ((tokens ?? []) as MarkedToken[]).flatMap((t): Block[] => {
    switch (t.type) {
      case "space":
      case "def":
        return [];
      case "heading":
        return [{ kind: "heading", level: Math.min(Math.max(t.depth, 1), 6) as HeadingLevel, children: inlines(t.tokens) }];
      case "paragraph":
        return [{ kind: "paragraph", children: inlines(t.tokens) }];
      case "text":
        return [{ kind: "paragraph", children: t.tokens ? inlines(t.tokens) : [decode(t.text)] }];
      case "code":
        return t.lang === "qr" ? [{ kind: "qr", value: t.text.trim() }] : [{ kind: "code", lang: t.lang ?? "", text: t.text }];
      case "list":
        return [{ kind: "list", ordered: t.ordered, start: t.start === "" ? 1 : t.start, items: t.items.map((i) => item(i.tokens)) }];
      case "table":
        return [{
          kind: "table",
          align: t.align,
          header: t.header.map((cell) => inlines(cell.tokens)),
          rows: t.rows.map((row) => row.map((cell) => inlines(cell.tokens))),
        }];
      case "blockquote":
        return [{ kind: "quote", children: blocks(t.tokens) }];
      case "hr":
        return [{ kind: "hr" }];
      default:
        return [{ kind: "paragraph", children: [t.raw.trimEnd()] }];
    }
  });
}

export function parseDoc(source: string): Block[] {
  return blocks(lexer(source));
}

function plain(nodes: Inline[]): string {
  return nodes
    .map((n) => (typeof n === "string" ? n : "children" in n ? plain(n.children) : n.kind === "code" ? n.text : n.kind === "image" ? n.alt : ""))
    .join("");
}

export function pageTitle(name: string, source: string | undefined): string {
  const heading = source === undefined ? undefined : parseDoc(source).find((b) => b.kind === "heading" && b.level === 1);
  return (heading?.kind === "heading" && plain(heading.children).trim()) || name;
}
