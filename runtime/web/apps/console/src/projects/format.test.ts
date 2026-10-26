import { describe, expect, it } from "vitest";
import { deleteRows, elapsed, folderHeading, relativeTime, size, subtitle } from "./format";

describe("elapsed", () => {
  it("never shows negative time when the VM clock runs ahead", () => {
    expect(elapsed(1_000, 999_000)).toBe("0:00");
  });

  it("shows minutes and padded seconds under an hour", () => {
    expect(elapsed(0, 72_000)).toBe("1:12");
    expect(elapsed(0, 3_599_000)).toBe("59:59");
  });

  it("adds hours from sixty minutes", () => {
    expect(elapsed(0, 3_600_000)).toBe("1:00:00");
    expect(elapsed(0, 3_723_000)).toBe("1:02:03");
  });
});

describe("subtitle", () => {
  it("counts projects waiting on the agent, and ignores the count at zero", () => {
    expect(subtitle(6, 3, 2, "Claude Code")).toBe("Six on the go · two waiting on Claude Code");
    expect(subtitle(6, 3, 0, "Claude Code")).toBe(subtitle(6, 3));
    expect(subtitle(2, 0, 1)).toBe("Two on the go · one waiting on your coding agent");
  });

  it("spells counts up to nine and uses digits after", () => {
    expect(subtitle(4, 2)).toBe("Four on the go · two of them cooking");
    expect(subtitle(9, 1)).toBe("Nine on the go · one of them cooking");
    expect(subtitle(10, 10)).toBe("10 on the go · 10 of them cooking");
  });

  it("leaves out the cooking clause when nothing runs", () => {
    expect(subtitle(3, 0)).toBe("Three on the go");
  });

  it("has its own line for no projects", () => {
    expect(subtitle(0, 0)).toBe("Nothing on the go yet");
  });
});

describe("folderHeading", () => {
  it("is singular for one folder and counted otherwise", () => {
    expect(folderHeading(1)).toBe("A folder turned up");
    expect(folderHeading(2)).toBe("Two folders turned up");
    expect(folderHeading(12)).toBe("12 folders turned up");
  });
});

describe("size", () => {
  it("uses the largest whole unit with one decimal below ten", () => {
    expect(size(0)).toBe("0 B");
    expect(size(1023)).toBe("1023 B");
    expect(size(1024)).toBe("1 KB");
    expect(size(1536)).toBe("1.5 KB");
    expect(size(38 * 1024 * 1024)).toBe("38 MB");
    expect(size(5 * 1024 ** 3)).toBe("5 GB");
  });
});

describe("relativeTime", () => {
  it("rounds down to the largest unit", () => {
    expect(relativeTime(1_000, 1_030_000)).toBe("just now");
    expect(relativeTime(1_000, 1_060_000)).toBe("1 minute ago");
    expect(relativeTime(1_000, 1_240_000)).toBe("4 minutes ago");
    expect(relativeTime(0, 7_200_000)).toBe("2 hours ago");
    expect(relativeTime(0, 86_400_000)).toBe("1 day ago");
  });

  it("says just now for a time slightly in the future", () => {
    expect(relativeTime(2_000, 1_000_000)).toBe("just now");
  });
});

describe("deleteRows", () => {
  it("lists files, machines and stored data with real names", () => {
    expect(
      deleteRows({ files: 412, bytes: 38 * 1024 * 1024, containers: ["shop-web-1"], volumes: ["shop_db"] }),
    ).toEqual([
      { label: "Every file in the project", detail: "412 files · 38 MB" },
      { label: "The little machines that run it", detail: "stopped & removed", names: ["shop-web-1"] },
      { label: "Its stored data", detail: "deleted", names: ["shop_db"] },
    ]);
  });

  it("leaves out anything there is none of", () => {
    expect(deleteRows({ files: 1, bytes: 10, containers: [], volumes: [] })).toEqual([
      { label: "Every file in the project", detail: "1 file · 10 B" },
    ]);
    expect(deleteRows({ files: 0, bytes: 0, containers: [], volumes: [] })).toEqual([]);
  });
});
