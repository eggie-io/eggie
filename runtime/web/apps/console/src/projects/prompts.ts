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
  full: ["What it does", "How it runs", "What's broken", "Keys it needs", "Top 3 fixes"],
  start: ["The cause, in plain words", "The fix it suggests", "It waits for your OK"],
  share: ["Leaked keys or data", "What breaks for others", "What to fix first"],
};

export function analyzePrompt(focus: Focus, id: string, detail: string | null): string {
  const p = folder(id);
  switch (focus) {
    case "full":
      return `Look through ${p} and give me a short, plain-language report. Don't change any files. Tell me: 1) what it does, in two sentences; 2) how it runs — what starts and what it needs; 3) what's broken or half-finished; 4) which keys or passwords it expects; 5) the three things you'd fix first, and why.`;
    case "start": {
      const ask = `${p} won't start in Eggie. Find out why: check docker-compose.yml, the Dockerfile${detail ? " and the error below" : ""}. Don't change any files yet. Explain the cause in plain words, tell me exactly what you'd change to fix it, then wait for my go-ahead.`;
      return detail ? `${ask}\n\n${detail}` : ask;
    }
    case "share":
      return `I'm about to put ${p} on a public address. Check it first, without changing any files: are there keys, passwords or personal data in the code or in anything it serves? What breaks for someone who isn't me? Give me a short list: what's fine, and what to fix before sharing.`;
  }
}
