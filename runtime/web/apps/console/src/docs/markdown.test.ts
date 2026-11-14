import { describe, expect, it } from "vitest";
import { pageTitle, parseDoc, type Block, type Inline } from "./markdown";

function text(nodes: (Block | Inline)[]): string {
  return nodes
    .map((n) => {
      if (typeof n === "string") return n;
      if ("children" in n) return text(n.children as (Block | Inline)[]);
      if ("text" in n) return n.text;
      return "";
    })
    .join("");
}

const kinds = (nodes: (Block | Inline)[]): string[] =>
  nodes.flatMap((n) => (typeof n === "string" ? [] : [n.kind, ...("children" in n ? kinds(n.children as (Block | Inline)[]) : [])]));

describe("parseDoc", () => {
  it("shows a raw HTML block as its own text, never as markup", () => {
    const doc = parseDoc("<script>alert(1)</script>\n");
    expect(text(doc)).toContain("<script>alert(1)</script>");
    expect(kinds(doc).every((k) => k === "paragraph")).toBe(true);
  });

  it("shows inline HTML as text inside the paragraph", () => {
    const [p] = parseDoc('Hi <b onclick="x">there</b>');
    expect(p.kind).toBe("paragraph");
    expect(p.kind === "paragraph" && p.children.every((c) => typeof c === "string")).toBe(true);
    expect(text([p])).toBe('Hi <b onclick="x">there</b>');
  });

  it("keeps &, < and > literal in text and code", () => {
    expect(text(parseDoc("Q&A and `a < b && c`"))).toBe("Q&A and a < b && c");
  });

  it("decodes character references in text, so &amp; reads as &", () => {
    expect(text(parseDoc("Fish &amp; chips&nbsp;&#8212; &#x2713; &bogus;"))).toBe("Fish & chips — ✓ &bogus;");
  });

  it("marks a task item at the start of its text instead of a separate line", () => {
    const [list] = parseDoc("- [ ] one\n- [x] two");
    expect(list.kind === "list" && list.items.map((item) => item.map((b) => text([b])))).toEqual([["☐ one"], ["☑ two"]]);
  });

  it("turns a qr code block into a QR of its trimmed text", () => {
    expect(parseDoc("```qr\nexp://192.168.1.5:8081\n```")).toEqual([{ kind: "qr", value: "exp://192.168.1.5:8081" }]);
  });

  it("resolves links to pages and refuses script links", () => {
    const [p] = parseDoc("[Stores](stores.md) [x](javascript:alert(1))");
    const links = p.kind === "paragraph" ? p.children.filter((c) => typeof c !== "string" && c.kind === "link") : [];
    expect(links.map((l) => typeof l !== "string" && l.kind === "link" && l.target)).toEqual([{ kind: "page", page: "stores" }, { kind: "none" }]);
  });

  it("resolves an image against the docs folder", () => {
    const [p] = parseDoc("![QR](qr.png)");
    expect(p).toEqual({ kind: "paragraph", children: [{ kind: "image", path: ".eggie/docs/qr.png", alt: "QR" }] });
  });

  it("nests a list inside a list item", () => {
    const [list] = parseDoc("- a\n  - b\n- c");
    expect(list.kind === "list" && list.items.length).toBe(2);
    expect(list.kind === "list" && list.items[0].map((b) => b.kind)).toEqual(["paragraph", "list"]);
  });

  it("keeps a table's column alignment and rows", () => {
    const [table] = parseDoc("| a | b |\n|:-|-:|\n| 1 | 2 |");
    expect(table.kind).toBe("table");
    if (table.kind !== "table") return;
    expect(table.align).toEqual(["left", "right"]);
    expect(table.header.map((c) => text(c))).toEqual(["a", "b"]);
    expect(table.rows.map((r) => r.map((c) => text(c)))).toEqual([["1", "2"]]);
  });
});

describe("pageTitle", () => {
  it("titles a page by its first level-one heading, else by its name", () => {
    expect(pageTitle("deploy", "Intro\n# Ship it \n# Second")).toBe("Ship it");
    expect(pageTitle("deploy", "## Sub only")).toBe("deploy");
    expect(pageTitle("deploy", undefined)).toBe("deploy");
  });

  it("skips a # line inside a code block", () => {
    expect(pageTitle("deploy", "```bash\n# not a title\n```\n# Real")).toBe("Real");
  });

  it("titles with the heading's text as shown, not its Markdown", () => {
    expect(pageTitle("menu", "# Fish &amp; *chips*")).toBe("Fish & chips");
    expect(pageTitle("menu", "Setext\n===")).toBe("Setext");
  });

  it("maps an action block to its id", () => {
    expect(parseDoc("```action\n ios-build \n```")).toEqual([{ kind: "action", id: "ios-build" }]);
  });

  it("takes the first non-empty line of an action block and maps an empty one to an empty id", () => {
    expect(parseDoc("```action\n\n  tunnel\nignored\n```")).toEqual([{ kind: "action", id: "tunnel" }]);
    expect(parseDoc("```action\n```")).toEqual([{ kind: "action", id: "" }]);
  });
});
