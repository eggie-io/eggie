import { Badge } from "./Badge";

export type SyncState = "synced" | "offline";

export function SyncMarker({ state, label }: { state: SyncState; label?: string }) {
  return (
    <Badge tone={state === "synced" ? "success" : "outline"} icon="cloud">
      {label ?? (state === "synced" ? "Synced · just now" : "Offline · will catch up later")}
    </Badge>
  );
}
