import type { Entry } from "./plan";

const BLOCK = 512;
const encoder = new TextEncoder();

function write(block: Uint8Array, at: number, text: string): void {
  block.set(encoder.encode(text), at);
}

// ustar numeric field: zero-padded octal, NUL-terminated.
function octal(value: number, width: number): string {
  return `${value.toString(8).padStart(width - 1, "0")}\0`;
}

function header(name: string, size: number, type: "0" | "x", mtime: number): Uint8Array<ArrayBuffer> {
  const block = new Uint8Array(BLOCK);
  write(block, 0, name);
  write(block, 100, octal(0o644, 8));
  write(block, 108, octal(0, 8));
  write(block, 116, octal(0, 8));
  write(block, 124, octal(size, 12));
  write(block, 136, octal(mtime, 12));
  // The checksum is computed with its own field read as spaces.
  block.fill(0x20, 148, 156);
  write(block, 156, type);
  write(block, 257, "ustar\0");
  write(block, 263, "00");
  let sum = 0;
  for (const byte of block) sum += byte;
  write(block, 148, `${sum.toString(8).padStart(6, "0")}\0 `);
  return block;
}

const ascii = (text: string) => /^[\x20-\x7e]*$/.test(text);

// A `path=` record whose length prefix counts itself.
function paxRecord(path: string): Uint8Array<ArrayBuffer> {
  const body = ` path=${path}\n`;
  const bodyLength = encoder.encode(body).byteLength;
  let total = bodyLength + String(bodyLength).length;
  while (String(total).length + bodyLength !== total) total = String(total).length + bodyLength;
  return encoder.encode(`${total}${body}`);
}

function padding(size: number): Uint8Array<ArrayBuffer> {
  return new Uint8Array((BLOCK - (size % BLOCK)) % BLOCK);
}

// Names that fit the 100-byte ustar field as plain ASCII go there; anything
// longer or non-ASCII goes in a PAX header the real entry follows, which every
// reader (Python's tarfile included) prefers over the field.
function* headers(path: string, size: number, mtime: number): Generator<Uint8Array<ArrayBuffer>> {
  if (path.length <= 100 && ascii(path)) {
    yield header(path, size, "0", mtime);
    return;
  }
  const record = paxRecord(path);
  yield header("PaxHeader/entry", record.byteLength, "x", mtime);
  yield record;
  yield padding(record.byteLength);
  const placeholder = path.replace(/[^\x20-\x7e]/g, "_").slice(-100);
  yield header(placeholder, size, "0", mtime);
}

// A tar of references, not bytes: the headers are built here and every file
// stays a part of the Blob, so the browser streams it from disk while sending.
// Reading and gzipping in the page held a 1.8 GB folder in memory several
// times over and crashed it.
export function tarBlob(entries: readonly Entry[]): Blob {
  const mtime = Math.floor(Date.now() / 1000);
  const parts: BlobPart[] = [];
  for (const entry of entries) {
    parts.push(...headers(entry.path, entry.size, mtime), entry.file, padding(entry.size));
  }
  parts.push(new Uint8Array(BLOCK * 2));
  return new Blob(parts, { type: "application/x-tar" });
}
