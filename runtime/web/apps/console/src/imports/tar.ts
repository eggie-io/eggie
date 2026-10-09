import type { Entry } from "./plan";

const BLOCK = 512;
const encoder = new TextEncoder();

export class FileChanged extends Error {
  readonly path: string;

  constructor(path: string) {
    super(`${path} changed while it was being packed`);
    this.name = "FileChanged";
    this.path = path;
  }
}

function write(block: Uint8Array, at: number, text: string): void {
  block.set(encoder.encode(text), at);
}

// ustar numeric field: zero-padded octal, NUL-terminated.
function octal(value: number, width: number): string {
  return `${value.toString(8).padStart(width - 1, "0")}\0`;
}

function header(name: string, size: number, type: "0" | "x", mtime: number): Uint8Array {
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
function paxRecord(path: string): Uint8Array {
  const body = ` path=${path}\n`;
  const bodyLength = encoder.encode(body).byteLength;
  let total = bodyLength + String(bodyLength).length;
  while (String(total).length + bodyLength !== total) total = String(total).length + bodyLength;
  return encoder.encode(`${total}${body}`);
}

function padding(size: number): Uint8Array {
  return new Uint8Array((BLOCK - (size % BLOCK)) % BLOCK);
}

// Names that fit the 100-byte ustar field as plain ASCII go there; anything
// longer or non-ASCII goes in a PAX header the real entry follows, which every
// reader (Python's tarfile included) prefers over the field.
function* headers(path: string, size: number, mtime: number): Generator<Uint8Array> {
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

// Each file read is a round trip to the browser process (about 1.7 ms in
// Chrome), so small files are read this many at a time, ahead of the writer.
// Large ones are streamed so a video never sits in memory whole.
const READ_AHEAD = 32;
const SMALL = 1024 * 1024;

async function* blocks(entries: readonly Entry[], onRead: (bytes: number) => void): AsyncGenerator<Uint8Array> {
  const mtime = Math.floor(Date.now() / 1000);
  const ahead = new Map<number, Promise<Uint8Array>>();
  let queued = 0;
  for (let i = 0; i < entries.length; i += 1) {
    for (; queued < entries.length && queued < i + READ_AHEAD; queued += 1) {
      if (entries[queued].size > SMALL) continue;
      const read = entries[queued].file.arrayBuffer().then((buffer) => new Uint8Array(buffer));
      // Awaited in order below; this only stops an early failure from being reported as unhandled.
      read.catch(() => {});
      ahead.set(queued, read);
    }
    const entry = entries[i];
    yield* headers(entry.path, entry.size, mtime);
    const small = ahead.get(i);
    if (small !== undefined) {
      ahead.delete(i);
      const bytes = await small;
      if (bytes.byteLength !== entry.size) throw new FileChanged(entry.path);
      onRead(bytes.byteLength);
      yield bytes;
      yield padding(entry.size);
      continue;
    }
    // The header already promised `size` bytes; a file edited since it was
    // picked would corrupt everything after it, so the mismatch is an error.
    let read = 0;
    const reader = entry.file.stream().getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      read += value.byteLength;
      if (read > entry.size) {
        await reader.cancel();
        throw new FileChanged(entry.path);
      }
      onRead(value.byteLength);
      yield value;
    }
    if (read !== entry.size) throw new FileChanged(entry.path);
    yield padding(entry.size);
  }
  yield new Uint8Array(BLOCK * 2);
}

export function tarStream(entries: readonly Entry[], onRead: (bytes: number) => void = () => {}): ReadableStream<Uint8Array> {
  const source = blocks(entries, onRead);
  return new ReadableStream({
    async pull(controller) {
      try {
        const { done, value } = await source.next();
        if (done) controller.close();
        else controller.enqueue(value);
      } catch (error) {
        controller.error(error);
      }
    },
    cancel() {
      void source.return(undefined);
    },
  });
}

// A Blob, not a streamed request body: fetch() cannot stream an upload over
// plain HTTP/1.1, which is what the console is served on.
export function packGzip(entries: readonly Entry[], onRead?: (bytes: number) => void): Promise<Blob> {
  // lib.dom types the compressor's input as BufferSource, wider than the bytes it is fed.
  const gzip = new CompressionStream("gzip") as unknown as ReadableWritablePair<Uint8Array, Uint8Array>;
  return new Response(tarStream(entries, onRead).pipeThrough(gzip)).blob();
}
