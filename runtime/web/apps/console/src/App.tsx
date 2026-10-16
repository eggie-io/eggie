import { useCallback, useEffect, useRef, useState } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { createQueryClient } from "./api/queryClient";
import { boot, type BootResult } from "./boot/boot";
import { AgentGuide } from "./screens/agents/AgentGuide";
import { AgentPicker } from "./screens/agents/AgentPicker";
import { Kit } from "./screens/Kit";
import { FilesPage } from "./screens/files/FilesPage";
import { SecretsPage } from "./screens/secrets/SecretsPage";
import { NeedsUpdate } from "./screens/NeedsUpdate";
import { NotAnswering } from "./screens/NotAnswering";
import { ProjectList } from "./screens/list/ProjectList";
import { ProjectPage } from "./screens/project/ProjectPage";
import { SignIn } from "./screens/account/SignIn";
import { SignedOut } from "./screens/SignedOut";
import { WrongHost } from "./screens/WrongHost";
import { DesktopHome } from "./desktop/DesktopContext";
import { AccountMenu } from "./shell/AccountMenu";
import { Shell } from "./shell/Shell";
import { Tabs } from "./shell/Tabs";
import { OnboardingProvider, Progress, SkipLink, useOnboarding } from "./screens/onboarding/Onboarding";
import { isLocalHost, nextSite, nextWebApp, SiteContext, WebAppContext } from "./site/SiteContext";
import { QueueProvider } from "./uploads/QueueProvider";

export function App({ handoff, home = null }: { handoff: string | null; home?: string | null }) {
  return (
    <DesktopHome.Provider value={home}>
      <Screens handoff={handoff} />
    </DesktopHome.Provider>
  );
}

function Screens({ handoff }: { handoff: string | null }) {
  const [result, setResult] = useState<BootResult | null>(null);
  // A local address starts as null so localhost doesn't shift layout while
  // health is pending.
  const [site, setSite] = useState<string | null | undefined>(
    isLocalHost(window.location.hostname) ? null : undefined,
  );
  const [webApp, setWebApp] = useState<string | null | undefined>(undefined);
  const pendingHandoff = useRef(handoff);
  const running = useRef(false);

  const run = useCallback(async () => {
    if (running.current) return;
    running.current = true;
    // Emptied before the first await: the code is single-use, and StrictMode
    // runs this effect twice in development.
    const code = pendingHandoff.current;
    pendingHandoff.current = null;
    try {
      const next = await boot({ fetch: (input, init) => fetch(input, init), handoff: code });
      setSite((prev) => nextSite(prev, next));
      setWebApp((prev) => nextWebApp(prev, next));
      setResult(next);
    } finally {
      running.current = false;
    }
  }, []);

  useEffect(() => {
    void run();
  }, [run]);

  const [queryClient] = useState(() =>
    createQueryClient((reason) => setResult({ kind: "signedOut", reason })),
  );

  const needAccount = useCallback(() => setResult({ kind: "needsAccount" }), []);

  return (
    <SiteContext.Provider value={site}>
      <WebAppContext.Provider value={webApp}>{screen()}</WebAppContext.Provider>
    </SiteContext.Provider>
  );

  function screen() {
    if (result === null) return <Shell />;
    return renderResult(result);
  }

  function renderResult(result: BootResult) {
    switch (result.kind) {
      case "signedIn":
        return (
          <QueryClientProvider client={queryClient}>
            <QueueProvider onSessionLost={(reason) => setResult({ kind: "signedOut", reason })}>
              <BrowserRouter>
                <OnboardingProvider>
                  <Console onSignedOut={needAccount} />
                </OnboardingProvider>
              </BrowserRouter>
            </QueueProvider>
          </QueryClientProvider>
        );
      case "needsAccount":
        return <SignIn onSignedIn={run} onSessionLost={(reason) => setResult({ kind: "signedOut", reason })} />;
      case "signedOut":
        return <SignedOut reason={result.reason} onRetry={run} />;
      case "needsUpdate":
        return <NeedsUpdate apiVersion={result.apiVersion} onRetry={run} />;
      case "notAnswering":
        return <NotAnswering onRetry={run} />;
      case "wrongHost":
        return <WrongHost onRetry={run} />;
    }
  }
}

function Console({ onSignedOut }: { onSignedOut: () => void }) {
  const { phase } = useOnboarding();
  const welcome = phase === "onboarding";
  return (
    <Shell
      signedIn
      nav={welcome ? <Progress /> : <Tabs />}
      trailing={
        <>
          {welcome && <SkipLink />}
          <AccountMenu onSignedOut={onSignedOut} />
        </>
      }
    >
      {phase === "deciding" ? null : welcome ? (
        <Routes>
          <Route path="/welcome" element={<AgentPicker base="/welcome" />} />
          <Route path="/welcome/:id" element={<AgentGuide base="/welcome" />} />
          <Route path="*" element={<Navigate to="/welcome" replace />} />
        </Routes>
      ) : (
        <Routes>
          <Route path="/" element={<ProjectList />} />
          <Route path="/p/:id" element={<ProjectPage />} />
          <Route path="/p/:id/files/*" element={<FilesPage />} />
          <Route path="/p/:id/secrets" element={<SecretsPage />} />
          <Route path="/agents" element={<AgentPicker base="/agents" />} />
          <Route path="/agents/:id" element={<AgentGuide base="/agents" />} />
          <Route path="/kit" element={<Kit />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      )}
    </Shell>
  );
}
