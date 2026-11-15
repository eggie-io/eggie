export type ActionType = "one-off" | "long-running";

export type Run = { state: "running" | "exited"; exit_code: number | null; started_at: number; timed_out: boolean };

export type Action = {
  id: string;
  recipe: string;
  name: string;
  description: string | null;
  command: string;
  type: ActionType;
  run: Run | null;
  connections: string[];
  asks: boolean;
};

export type ActionProblem = { recipe: string | null; code: string; message: string };

export type ConnectionInfo = { id: string; name: string; description: string | null; login: boolean; check: boolean };

export type ActionsAnswer = { actions: Action[]; problems: ActionProblem[]; connections: ConnectionInfo[] };

export type ConnectionState = "connected" | "signed-out" | "checking" | "unknown";

export type Asking = { source: string; id: string; label: string; type: "text" | "secret" };

export type ConnectionRun = Run & { link: string | null; code: string | null; asking: Asking | null };

export type ConnectionAnswer = { id: string; name: string; state: ConnectionState; run: ConnectionRun | null };

export type ActionRun = { run: Run | null; asking: Asking | null };

export type AnswerBody = { source: string; question: string; value: string };

export type TerminalTarget = { kind: "action"; action: Action } | { kind: "connection"; id: string; name: string };
