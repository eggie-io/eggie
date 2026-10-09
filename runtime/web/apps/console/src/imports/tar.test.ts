import { gunzipSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import type { Entry } from "./plan";
import { FileChanged, packGzip, tarStream } from "./tar";

const decoder = new TextDecoder();

// A reader written from the ustar/PAX spec, not from tar.ts: it checks the
// checksum, the 512-byte framing and the PAX `path` override independently.
interface Member {
  name: string;
  size: number;
  content: string;
}

function readTar(bytes: Uint8Array): Member[] {
  const members: Member[] = [];
  let at = 0;
  let paxPath: string | null = null;
  const field = (start: number, length: number) => decoder.decode(bytes.subarray(at + start, at + start + length)).replace(/\0.*$/s, "");
  for (;;) {
    const block = bytes.subarray(at, at + 512);
    if (block.every((b) => b === 0)) break;
    let sum = 0;
    for (let i = 0; i < 512; i += 1) sum += i >= 148 && i < 156 ? 0x20 : block[i];
    expect(parseInt(field(148, 8), 8), `checksum at ${at}`).toBe(sum);
    expect(field(257, 6)).toBe("ustar");
    const size = parseInt(field(124, 12), 8);
    const type = String.fromCharCode(block[156]);
    const name = field(0, 100);
    const data = bytes.subarray(at + 512, at + 512 + size);
    at += 512 + Math.ceil(size / 512) * 512;
    if (type === "x") {
      const text = decoder.decode(data);
      const match = /^(\d+) path=(.*)\n$/s.exec(text);
      expect(match, "a well-formed pax record").not.toBeNull();
      expect(Number(match![1])).toBe(new TextEncoder().encode(text).byteLength);
      paxPath = match![2];
      continue;
    }
    members.push({ name: paxPath ?? name, size, content: decoder.decode(data) });
    paxPath = null;
  }
  expect(bytes.length - at, "two zero blocks at the end").toBe(1024);
  expect(bytes.length % 512).toBe(0);
  return members;
}

const entry = (path: string, content: string, size = content.length): Entry => ({ path, size, file: new Blob([content]) });

async function tarBytes(entries: Entry[]): Promise<Uint8Array> {
  return new Uint8Array(await new Response(tarStream(entries)).arrayBuffer());
}

describe("tarStream", () => {
  it("frames each file in 512-byte blocks that an independent reader gets back intact", async () => {
    const odd = "x".repeat(513);
    const members = readTar(await tarBytes([entry("src/main.py", "print(1)\n"), entry("big.bin", odd), entry("empty.txt", "")]));
    expect(members).toEqual([
      { name: "src/main.py", size: 9, content: "print(1)\n" },
      { name: "big.bin", size: 513, content: odd },
      { name: "empty.txt", size: 0, content: "" },
    ]);
  });

  it("carries a long or non-ASCII path in a PAX record", async () => {
    const long = `${"deep/".repeat(25)}file.txt`;
    const members = readTar(await tarBytes([entry(long, "a"), entry("docs/Звіт.md", "b"), entry("plain.md", "c")]));
    expect(members.map((m) => m.name)).toEqual([long, "docs/Звіт.md", "plain.md"]);
    expect(members.map((m) => m.content)).toEqual(["a", "b", "c"]);
  });

  it("keeps archive order when files read ahead finish out of order", async () => {
    // Earlier files answer last, the way a slow disk read would.
    const slow = (content: string, delayMs: number) =>
      ({
        size: content.length,
        arrayBuffer: () => new Promise((resolve) => setTimeout(() => resolve(new TextEncoder().encode(content).buffer), delayMs)),
        stream: () => new Blob([content]).stream(),
      }) as unknown as Blob;
    const entries = ["a", "b", "c", "d"].map((name, i) => ({ path: `${name}.txt`, size: 1, file: slow(name, 40 - i * 10) }));
    const members = readTar(await tarBytes(entries));
    expect(members.map((m) => `${m.name}=${m.content}`)).toEqual(["a.txt=a", "b.txt=b", "c.txt=c", "d.txt=d"]);
  });

  it("streams a large file between small ones without mixing them up", async () => {
    const big = "y".repeat(1024 * 1024 + 7);
    const members = readTar(await tarBytes([entry("a.txt", "a"), entry("big.bin", big), entry("z.txt", "z")]));
    expect(members.map((m) => [m.name, m.size])).toEqual([["a.txt", 1], ["big.bin", big.length], ["z.txt", 1]]);
    expect(members[1].content === big).toBe(true);
  });

  it("an empty folder is just the end-of-archive marker", async () => {
    expect(readTar(await tarBytes([]))).toEqual([]);
  });

  it("refuses a file whose size no longer matches what was planned", async () => {
    await expect(tarBytes([entry("grew.txt", "hello", 3)])).rejects.toBeInstanceOf(FileChanged);
    await expect(tarBytes([entry("shrank.txt", "hi", 10)])).rejects.toBeInstanceOf(FileChanged);
  });
});

describe("packGzip", () => {
  it("is a gzip whose contents are the tar, and reports bytes as they are read", async () => {
    const read: number[] = [];
    const blob = await packGzip([entry("a.txt", "hello"), entry("b.txt", "world!")], (n) => read.push(n));
    const bytes = new Uint8Array(await blob.arrayBuffer());
    expect([bytes[0], bytes[1]]).toEqual([0x1f, 0x8b]);
    expect(readTar(new Uint8Array(gunzipSync(bytes))).map((m) => m.content)).toEqual(["hello", "world!"]);
    expect(read.reduce((a, b) => a + b, 0)).toBe(11);
  });
});
