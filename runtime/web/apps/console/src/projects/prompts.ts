import type { Project } from "./types";
import type { Cause, Source } from "./view";

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

const folder = (id: string) => `~/projects/${id}`;

export function newPrompt(id: string): string {
  return `I'm starting a new project in ${folder(id)}. The folder is empty. Ask me a few short questions about what it should do, then build it there. Add a docker-compose.yml at the top of the folder that starts everything, with the web service listening on 0.0.0.0, and start it with \`eggie up\`. When you're done, tell me in plain words what you made and how to use it.`;
}

const FROM: Record<Project["origin"], string> = { github: " from GitHub", folder: " from a folder", new: "" };

export function setupPrompt(id: string, origin: Project["origin"], detail: string | null, source: Source): string {
  const ask = `I copied ${folder(id)}${FROM[origin]} into Eggie and it doesn't start yet. Work out how this project is meant to run, then make it start with \`eggie up\`: add or fix docker-compose.yml at the top of the folder, with the web service listening on 0.0.0.0. Don't change what the app does. If it needs API keys or passwords, don't invent them. List each one so I can add it in Eggie → Secrets. Finish by telling me in plain words what you changed.`;
  return detail ? `${ask}\n\n${said(source, detail)}` : ask;
}

export const said = (source: Source, detail: string) => `${source === "docker" ? "Docker" : "Eggie"} said: ${detail}`;

export type Focus = "full" | "start" | "share";

export const ANALYZE_GETS: Record<Focus, string[]> = {
  full: ["Verdict", "What matters most", "What's in good shape", "What wasn't checked", "Keys it needs"],
  start: ["The cause, in plain words", "The fix it suggests", "It waits for your OK"],
  share: ["What strangers could reach", "Leaked keys", "What to change before sharing"],
};

// Without this an agent that lacks the skill answers free-form, and it reads like an audit.
const NO_SKILL = "If you don't have that skill, say so instead of guessing.";

export function analyzePrompt(focus: Focus, id: string, detail: string | null): string {
  const p = folder(id);
  switch (focus) {
    case "full":
      return `Use the eggie-audit skill to take a quick look at ${p} and tell me what shape it's in. Also tell me which keys or passwords it expects. Don't fix anything. ${NO_SKILL}`;
    case "start": {
      const files = detail ? "docker-compose.yml, the Dockerfile and the error below" : "docker-compose.yml and the Dockerfile";
      const ask = `${p} won't start in Eggie. Find out why: read \`eggie logs\` first, then ${files}. Don't change any files yet. Explain the cause in plain words, tell me exactly what you'd change to fix it, then wait for my go-ahead.`;
      return detail ? `${ask}\n\n${detail}` : ask;
    }
    case "share":
      return `I'm about to put ${p} on a public address, so other people will be able to open it. Use the eggie-audit skill to check security and readiness to go live first. Don't fix anything until I say so. ${NO_SKILL}`;
  }
}
