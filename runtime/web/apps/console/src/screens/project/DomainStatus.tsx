import { StatusDot, type DotStatus } from "@eggie/ui";
import { badgeTone, statusLabel } from "../../domains/domains";

const DOT: Record<ReturnType<typeof badgeTone>, DotStatus> = { ok: "running", busy: "starting", warning: "degraded" };

export function DomainStatus({ status }: { status: string }) {
  return <StatusDot status={DOT[badgeTone(status)]} label={statusLabel(status)} />;
}
