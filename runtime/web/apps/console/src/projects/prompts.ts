import type { Cause } from "./view";

export const ANALYZE_PROMPT = `Take a look around this project and tell me, in plain
language: what it does today, what's half-finished or
broken, and the three things you'd fix first. Don't
change any code yet — just tell me what you found.`;

const CLOSE = "Keep the change as small as you can, then tell me in plain words what you changed.";

export function fixPrompt(cause: Cause, id: string, detail: string | null): string {
  const where = `The project in ~/projects/${id}`;
  switch (cause) {
    case "bound_to_loopback":
      return `${where} starts, but its web service only listens on 127.0.0.1 inside its container, so nothing outside the container can reach it. Make it listen on 0.0.0.0 instead. ${CLOSE}`;
    case "service_unreachable":
      return `${where} starts, but nothing answers on the port its web service is published on. Check that the port in docker-compose.yml is the one the app really listens on, and that the app doesn't exit after starting. ${CLOSE}`;
    case "crash_looping":
      return `${where} keeps restarting: one of its containers exits and starts again. Read its logs with docker compose logs in that folder, find why it exits, and fix it. ${CLOSE}`;
    case "failed_to_start":
      return `${where} won't start: Docker refuses to bring it up. Find out why from docker-compose.yml and the error Docker gives, and fix it. ${CLOSE}`;
    case "unreadable":
      return `${where} has a docker-compose.yml that Eggie can't read${detail ? ` (it says: ${detail})` : ""}. Make it a valid compose file that starts the project, with the web service listening on 0.0.0.0. ${CLOSE}`;
  }
}

export function waitingPrompt(id: string): string {
  return `I want to build something in ~/projects/${id}. Ask me what it should do, then build it, and add a docker-compose.yml at the top of that folder that starts it, with the web service listening on 0.0.0.0. Tell me in plain words what you made and how to use it.`;
}
