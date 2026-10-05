import { ApiError } from "../api/client";
import type { Project } from "./types";
import type { Cause } from "./view";

export function phaseCaption(phase: string, firstRun: boolean): string {
  switch (phase) {
    case "preparing":
      return "Getting the pan out";
    case "starting":
      return firstRun ? "Fetching the bits it needs" : "Starting it up";
    case "checking":
      return "Checking it answers";
    case "cloning":
      return "Downloading from GitHub";
    default:
      return "Working on it";
  }
}

export const CAUSE_COPY: Record<Cause, { heading: (id: string) => string; short: string; body: string }> = {
  bound_to_loopback: {
    heading: (id) => `${id} started, but it isn't answering`,
    short: "Started, but it isn't answering the door",
    body:
      "Your app is awake — it's just talking to itself. It needs to listen for visitors coming from outside its own " +
      "box, and right now it only listens to the inside. Your coding agent can change that in one line.",
  },
  service_unreachable: {
    heading: (id) => `${id} started, but nothing answers at its address`,
    short: "Started, but nothing answers at its address",
    body:
      "Its little machines are up, but nothing replies where Eggie sends visitors. It may be listening on a " +
      "different port, or it fell over after starting.",
  },
  crash_looping: {
    heading: (id) => `${id} keeps falling over`,
    short: "Keeps falling over",
    body: "It starts, trips, and starts again. The raw details below usually say why — your coding agent can read them.",
  },
  failed_to_start: {
    heading: (id) => `${id} couldn't start`,
    short: "Couldn't start",
    body: "Docker refused to start it. The raw details below say what it tripped on.",
  },
  unreadable: {
    heading: (id) => `Eggie can't read ${id}'s start-up recipe`,
    short: "Eggie can't read its start-up recipe",
    body: "The docker-compose.yml is there, but Eggie can't make sense of it.",
  },
};

export function actionError(error: unknown): string {
  if (error instanceof ApiError && error.code === "project_busy") return "It's already busy — give it a moment.";
  return error instanceof Error ? error.message : "Something went wrong.";
}

export function primaryUrl(project: Project): string | null {
  return project.web.find((entry) => entry.primary)?.url ?? project.urls[0] ?? null;
}
