import type { ReactNode } from "react";
import { cx } from "../cx";
import s from "./StatusDot.module.css";

export type DotStatus = "running" | "starting" | "stopped" | "failed";

const WORD: Record<DotStatus, string> = {
  running: "Running",
  starting: "Starting",
  stopped: "Stopped",
  failed: "Failed",
};

export function StatusDot({ status, label }: { status: DotStatus; label?: ReactNode }) {
  return (
    <span className={s.status}>
      <span className={cx(s.dot, s[status])} aria-hidden="true" />
      {label ?? WORD[status]}
    </span>
  );
}
