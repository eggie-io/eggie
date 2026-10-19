import { StatusDot, type DotStatus } from "./StatusDot";

export type ProjectState = "running" | "stopped" | "starting" | "wrong";

const LABEL: Record<ProjectState, string> = {
  running: "Running",
  stopped: "Stopped",
  starting: "Starting",
  wrong: "Something's wrong",
};

const DOT: Record<ProjectState, DotStatus> = {
  running: "running",
  stopped: "stopped",
  starting: "starting",
  wrong: "failed",
};

export function StateBadge({ state }: { state: ProjectState }) {
  return <StatusDot status={DOT[state]} label={LABEL[state]} />;
}
