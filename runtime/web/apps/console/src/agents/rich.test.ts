import { describe, expect, it } from "vitest";
import { parseRich } from "./rich";

describe("parseRich", () => {
  it("keeps plain text as one string", () => {
    expect(parseRich("Open the app and sign in.")).toEqual(["Open the app and sign in."]);
  });

  it("builds a tree from the allowed tags", () => {
    expect(parseRich("<p>Fill in:</p><ul><li><strong>Host</strong></li><li>Port</li></ul>")).toEqual([
      { tag: "p", children: ["Fill in:"] },
      { tag: "ul", children: [{ tag: "li", children: [{ tag: "strong", children: ["Host"] }] }, { tag: "li", children: ["Port"] }] },
    ]);
  });

  it("takes <br> with or without a slash", () => {
    expect(parseRich("a<br>b<br/>c")).toEqual(["a", { tag: "br", children: [] }, "b", { tag: "br", children: [] }, "c"]);
  });

  it("decodes entities and keeps a lone ampersand", () => {
    expect(parseRich("Q&A &amp; &lt;key&gt; &#8594;")).toEqual(["Q&A & <key> →"]);
  });

  it("refuses attributes and tags outside the subset", () => {
    expect(parseRich('<p class="x">hi</p>')).toBeNull();
    expect(parseRich('<img src="x" onerror="alert(1)">')).toBeNull();
    expect(parseRich("<script>alert(1)</script>")).toBeNull();
    expect(parseRich('<a href="https://x">x</a>')).toBeNull();
  });

  it("refuses broken nesting and unknown entities", () => {
    expect(parseRich("<ul><li>a</ul>")).toBeNull();
    expect(parseRich("<p>open")).toBeNull();
    expect(parseRich("</p>")).toBeNull();
    expect(parseRich("&bogus;")).toBeNull();
  });
});
