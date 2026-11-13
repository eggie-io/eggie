import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef, useState } from "react";
import { Button, Modal, Notice } from "@eggie/ui";
import { useProjectActions } from "../../actions/queries";
import { closeReason, reconnectDelay, terminalUrl, type CloseReason } from "../../actions/socket";
import type { Action } from "../../actions/types";
import s from "./TerminalPanel.module.css";

const NOTES: Partial<Record<CloseReason, string>> = {
  refused: "Eggie couldn't open the terminal. Reload the page and sign in again.",
  unavailable: "The part of Eggie that runs actions isn't answering. Restart the machine from the desktop app and try again.",
  gone: "This action isn't running.",
};

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function TerminalPanel({ projectId, action, onClose }: { projectId: string; action: Action; onClose: () => void }) {
  const host = useRef<HTMLDivElement>(null);
  const [ended, setEnded] = useState<CloseReason | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const actions = useProjectActions(projectId);
  const run = actions.data?.actions.find((a) => a.id === action.id)?.run ?? null;

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const term = new Terminal({
      fontFamily: cssVar("--font-mono") || "monospace",
      fontSize: 13,
      cursorBlink: true,
      theme: { background: cssVar("--code-bg") || undefined, foreground: cssVar("--code-ink") || undefined },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(el);

    let socket: WebSocket | null = null;
    let timer: number | undefined;
    let attempt = 0;
    let disposed = false;
    // Only the very first handshake can be "refused"; later failures are a restart or outage.
    let everOpened = false;
    const encoder = new TextEncoder();

    const connect = () => {
      // The server redraws the screen on attach.
      term.reset();
      const ws = new WebSocket(terminalUrl(window.location, projectId, action.id, term.cols, term.rows));
      ws.binaryType = "arraybuffer";
      socket = ws;
      ws.onopen = () => {
        everOpened = true;
        attempt = 0;
        setReconnecting(false);
      };
      ws.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) term.write(new Uint8Array(event.data));
      };
      ws.onclose = (event) => {
        if (disposed || socket !== ws) return;
        const reason = closeReason(event.code, everOpened);
        if (reason === "dropped") {
          setReconnecting(true);
          timer = window.setTimeout(connect, reconnectDelay(attempt++));
        } else {
          setReconnecting(false);
          setEnded(reason);
        }
      };
    };

    const input = term.onData((data) => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(encoder.encode(data));
    });
    const resized = term.onResize(({ cols, rows }) => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ resize: [cols, rows] }));
    });
    const observer = new ResizeObserver(() => {
      if (el.clientWidth > 0 && el.clientHeight > 0) fit.fit();
    });

    // The dialog only becomes visible after this effect; fitting earlier measures zero.
    const frame = requestAnimationFrame(() => {
      fit.fit();
      observer.observe(el);
      term.focus();
      connect();
    });

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      window.clearTimeout(timer);
      observer.disconnect();
      input.dispose();
      resized.dispose();
      socket?.close();
      term.dispose();
    };
  }, [projectId, action.id]);

  const finished = run?.state === "exited";
  return (
    <Modal open onClose={onClose} title={action.name} wide footer={<Button onClick={onClose}>Close</Button>}>
      <div className={s.body}>
        <div className={s.term} ref={host} />
        {finished && (
          <p className={s.status}>Finished — exit code {run.exit_code ?? "unknown"}</p>
        )}
        {reconnecting && <p className={s.status}>Reconnecting…</p>}
        {ended && NOTES[ended] && <Notice tone="danger">{NOTES[ended]}</Notice>}
      </div>
    </Modal>
  );
}
