import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { cx } from "@omelet/ui";
import { useAgentStatus } from "../../agents/queries";
import { decide, readMark, writeMark, type Phase } from "../../onboarding/onboarding";
import { useProjects } from "../../projects/queries";
import { CHECK } from "../icons";
import s from "./Onboarding.module.css";

const localStore = () => window.localStorage;

interface Onboarding {
  phase: Phase;
  // The agent that was connected during onboarding, for the empty counter's greeting.
  connectedAgent: string | null;
  finish: (connectedAgent?: string) => void;
}

const Context = createContext<Onboarding>({ phase: "off", connectedAgent: null, finish: () => {} });
export const useOnboarding = () => useContext(Context);

export function OnboardingProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>(() => (readMark(localStore) ? "off" : "deciding"));
  const [connectedAgent, setConnectedAgent] = useState<string | null>(null);
  const navigate = useNavigate();
  const finish = useCallback(
    (agent?: string) => {
      writeMark(localStore);
      setConnectedAgent(agent ?? null);
      setPhase("off");
      navigate("/", { replace: true });
    },
    [navigate],
  );
  const value = useMemo(() => ({ phase, connectedAgent, finish }), [phase, connectedAgent, finish]);
  return (
    <Context.Provider value={value}>
      {phase === "deciding" && <Decide onDecided={setPhase} />}
      {children}
    </Context.Provider>
  );
}

// Mounted only while deciding, so its queries stop polling once it's settled.
function Decide({ onDecided }: { onDecided: (phase: Phase) => void }) {
  const status = useAgentStatus();
  const projects = useProjects();
  const phase = decide({
    marked: false,
    statuses: status.data ?? (status.isError ? null : undefined),
    counterItems: projects.data
      ? projects.data.projects.length + projects.data.discovered.length
      : projects.isError ? null : undefined,
  });
  const failed = status.isError || projects.isError;
  useEffect(() => {
    if (phase === "deciding") return;
    // Someone already set up is past their first time, even if they later
    // empty the counter. A failed check decides nothing, so it leaves no mark.
    if (phase === "off" && !failed) writeMark(localStore);
    onDecided(phase);
  }, [phase, failed, onDecided]);
  return null;
}

const STEPS = ["Sign in", "Connect an agent", "First project"];
const CURRENT = 1;

export function Progress() {
  return (
    <ol className={s.progress} aria-label="Getting started">
      {STEPS.map((label, index) => (
        <li
          key={label}
          className={cx(s.item, index < CURRENT && s.done, index === CURRENT && s.current)}
          aria-current={index === CURRENT ? "step" : undefined}
        >
          <span className={s.dot}>{index < CURRENT ? CHECK : index + 1}</span>
          <span className={s.label}>{label}</span>
        </li>
      ))}
    </ol>
  );
}

export function SkipLink() {
  const { finish } = useOnboarding();
  return (
    <button type="button" className={s.skip} aria-label="Skip for now" onClick={() => finish()}>
      Skip<span className={s.skipTail}> for now</span>
    </button>
  );
}
