import { describe, expect, it } from "vitest";
import type { DirEntry } from "../uploads/queries";
import { docsRoute, pageNames, resolveImage, resolveLink } from "./pages";

const file = (name: string): DirEntry => ({ name, kind: "file", size: 1, items: null, modified: 0 });

describe("docs pages", () => {
  it("lists Markdown files only, index first and the rest by name ignoring case", () => {
    const entries = [file("beta.md"), file("notes.txt"), file("Alpha.md"), file("index.md"), { ...file("x.md"), kind: "folder" as const }, file("README.MD")];
    expect(pageNames(entries)).toEqual(["index", "Alpha", "beta"]);
  });

  it("opens web and mail links outside and sibling .md links as pages", () => {
    expect(resolveLink("https://expo.dev")).toEqual({ kind: "external", href: "https://expo.dev" });
    expect(resolveLink("mailto:a@b.c")).toEqual({ kind: "external", href: "mailto:a@b.c" });
    expect(resolveLink("stores.md#android")).toEqual({ kind: "page", page: "stores" });
    expect(resolveLink("./Deploy%20steps.md")).toEqual({ kind: "page", page: "Deploy steps" });
  });

  it("makes script, data, file, nested and anchor links plain text", () => {
    for (const href of ["javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,x", "file:///etc", "sub/x.md", "#top", "../x.md", "x.txt"]) {
      expect(resolveLink(href), href).toEqual({ kind: "none" });
    }
  });

  it("loads images relative to the docs folder and never outside the project", () => {
    expect(resolveImage("qr.png")).toBe(".eggie/docs/qr.png");
    expect(resolveImage("./img/qr.png")).toBe(".eggie/docs/img/qr.png");
    expect(resolveImage("../../shots/a.png")).toBe("shots/a.png");
    for (const href of ["../../../x.png", "/etc/x.png", "https://x/y.png", "data:image/png;base64,x", "//host/x.png", "%2E%2E/%2E%2E/%2E%2E/x.png"]) {
      expect(resolveImage(href), href).toBeNull();
    }
  });

  it("encodes the project and page in the route", () => {
    expect(docsRoute("my app")).toBe("/p/my%20app/docs");
    expect(docsRoute("my app", "Deploy steps")).toBe("/p/my%20app/docs/Deploy%20steps");
  });
});
