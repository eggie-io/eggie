export type ActionType = "one-off" | "long-running";

export type Run = { state: "running" | "exited"; exit_code: number | null; started_at: number };

export type Action = {
  id: string;
  recipe: string;
  name: string;
  description: string | null;
  command: string;
  type: ActionType;
  run: Run | null;
};

export type ActionProblem = { recipe: string | null; code: string; message: string };

export type ActionsAnswer = { actions: Action[]; problems: ActionProblem[] };
