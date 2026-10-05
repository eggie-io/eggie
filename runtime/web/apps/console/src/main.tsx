import "@eggie/ui/fonts";
import "@eggie/ui/tokens.css";
import "./app.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { takeHandoff } from "./boot/boot";
import { takeDesktopHome } from "./desktop/desktop";

function sessionStore(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

// Both read before anything renders or awaits: takeHandoff() clears the
// fragment, so a reload never resends the code.
const home = takeDesktopHome(window.location, sessionStore());
const handoff = takeHandoff(window.location, window.history);

async function start() {
  if (import.meta.env.DEV) {
    const { startMocks } = await import("./mocks/browser");
    await startMocks();
  }
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App handoff={handoff} home={home} />
    </StrictMode>,
  );
}

void start();
