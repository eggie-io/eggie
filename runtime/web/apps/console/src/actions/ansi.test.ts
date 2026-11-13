import { describe, expect, it } from "vitest";
import { stripAnsi } from "./ansi";

describe("stripAnsi", () => {
  it("drops colour, cursor and title sequences and keeps the text", () => {
    const raw = "\x1b[1;32mready\x1b[0m on \x1b]8;;http://x\x07link\x1b]8;;\x1b\\ \x1b[2K\x1b[?25lnow\x1b(B";
    expect(stripAnsi(raw)).toBe("ready on link now");
  });

  it("drops a sequence the tail cut off at the end", () => {
    expect(stripAnsi("done\x1b]0;tit")).toBe("done");
    expect(stripAnsi("done\x1b[3")).toBe("done");
  });

  it("leaves plain text alone", () => {
    expect(stripAnsi("a [b] ]c\n")).toBe("a [b] ]c\n");
  });
});
