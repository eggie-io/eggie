import { StatusDot, type DotStatus } from "./StatusDot";

export type ProjectState = "running" | "stopped" | "starting" | "wrong" | "waiting" | "setup";

const LABEL: Record<ProjectState, string> = {
  running: "Running",
  stopped: "Stopped",
  starting: "Starting",
  wrong: "Something’s wrong",
  waiting: "Waiting for agent",
  setup: "Needs set-up",
};

const DOT: Record<ProjectState, DotStatus> = {
  running: "running",
  stopped: "stopped",
  starting: "starting",
  wrong: "failed",
  waiting: "waiting",
  setup: "degraded",
};

export function StateBadge({ state }: { state: ProjectState }) {
  return <StatusDot status={DOT[state]} label={LABEL[state]} />;
}
