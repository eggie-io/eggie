import { describe, expect, it } from "vitest";
import { closeReason, terminalUrl } from "./socket";

describe("terminalUrl", () => {
  it("uses ws on http and wss on https, with encoded ids", () => {
    expect(terminalUrl({ protocol: "http:", host: "localhost:39080" }, "my app", "a/b", 80, 24))
      .toBe("ws://localhost:39080/api/projects/my%20app/actions/a%2Fb/terminal?cols=80&rows=24");
    expect(terminalUrl({ protocol: "https:", host: "foo.eggie.io" }, "p", "a", 100, 30).startsWith("wss://foo.eggie.io/")).toBe(true);
  });
});

describe("closeReason", () => {
  it("maps the API's close codes to what the panel does next", () => {
    expect(closeReason(4404, true)).toBe("gone");
    expect(closeReason(4409, true)).toBe("gone");
    expect(closeReason(4401, true)).toBe("refused");
    expect(closeReason(4403, true)).toBe("refused");
    expect(closeReason(1008, true)).toBe("refused");
    expect(closeReason(4503, true)).toBe("unavailable");
    expect(closeReason(1000, true)).toBe("ended");
  });

  it("treats a close before the socket ever opened as a refused handshake", () => {
    expect(closeReason(1006, false)).toBe("refused");
  });

  it("reconnects after any other loss", () => {
    for (const code of [1006, 1011, 4502, 1001]) expect(closeReason(code, true)).toBe("dropped");
  });
});
