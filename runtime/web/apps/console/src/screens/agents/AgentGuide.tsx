import { useId, useState } from "react";
import { Link, Navigate, useLocation, useNavigate, useParams } from "react-router";
import { BrandMark, Button, Card, Icon, Notice, cx } from "@eggie/ui";
import { firstStep, guideSteps, type Agent, type Connect, type Guide, type Step } from "../../agents/catalog";
import { cardRows } from "../../agents/card";
import { useAgents, useAgentStatus } from "../../agents/queries";
import { CopyButton } from "../../components/CopyButton";
import { desktopConnectUrl } from "../../desktop/desktop";
import { useDesktopAdded, useDesktopConnect, useDesktopHome } from "../../desktop/DesktopContext";
import { SkipButton, useOnboarding } from "../onboarding/Onboarding";
import { AgentMark } from "./AgentMark";
import { AgentMenu } from "./AgentMenu";
import type { AgentBase } from "./AgentPicker";
import { AgentStatusLine } from "./AgentStatusLine";
import { MediaSlider } from "./MediaSlider";
import { RichText } from "./RichText";
import s from "./Agents.module.css";

export function AgentGuide({ base }: { base: AgentBase }) {
  const { id } = useParams();
  const { platform, agents, connect, error } = useAgents();
  const [byHand, setByHand] = useState(false);

  if (error)
    return (
      <div className={s.page}>
        <p className={s.error}>Couldn't load the agent guides. Reload the page to try again.</p>
        {base === "/welcome" && <div><SkipButton /></div>}
      </div>
    );
  if (agents === undefined) return <div className={s.page}><p className={s.muted}>Getting the guide…</p></div>;
  const agent = agents.find((candidate) => candidate.id === id);
  if (!agent) return <Navigate to={base} replace />;

  return (
    <div className={cx(s.page, s.guidePage)}>
      <Link to={base} className={s.back}>
        <Icon name="chevron-left" />
        Agents
      </Link>
      <div className={s.guideHead}>
        <h1 className={s.guideTitle}>Connect a coding agent</h1>
        <AgentMenu agents={agents} current={agent} base={base} />
      </div>
      {/* Keyed so switching agent, or between the two ways, starts the list again. */}
      <Steps
        key={`${agent.id}:${byHand}`}
        agent={agent}
        guide={agent.platforms[platform]!}
        connect={connect}
        welcome={base === "/welcome"}
        byHand={byHand}
        setByHand={setByHand}
      />
    </div>
  );
}

// Shown on hover and on focus, so a keyboard or a tap reaches it too.
function Hint({ label, text }: { label: string; text: string }) {
  const id = useId();
  return (
    <span className={s.hint}>
      <button type="button" className={s.hintButton} aria-label={`About ${label}`} aria-describedby={id}>
        <Icon name="info" />
      </button>
      <span id={id} role="tooltip" className={s.hintText}>{text}</span>
    </span>
  );
}

// The desktop window, for an agent it can connect by itself. Undefined in a
// plain browser or for any other agent: the guide then shows only the manual steps.
function useConnectOffer(agent: Agent): { url: string; added: boolean } | undefined {
  const home = useDesktopHome();
  const connect = useDesktopConnect();
  const added = useDesktopAdded();
  const { pathname } = useLocation();
  if (!home || !connect.includes(agent.id)) return undefined;
  return { url: desktopConnectUrl(home, agent.id, pathname), added: added.includes(agent.id) };
}

// The automatic list's connect step, drawn in the picture frame: the button
// is the step. It sends the desktop window to its own screen, which adds the
// connection and brings the user straight back here.
function ConnectCard({ agent, step, offer }: { agent: Agent; step: Step; offer: { url: string; added: boolean } }) {
  const go = () => window.location.assign(offer.url);
  return (
    <div className={s.connectCard}>
      <div className={s.connectMarks} aria-hidden="true">
        <span className={s.mark}><BrandMark size={22} /></span>
        <Icon name="chevron-right" />
        <AgentMark agent={agent} />
      </div>
      {offer.added ? (
        <>
          <h2 className={s.connectTitle}><Icon name="check-circle" />Eggie is in {agent.name}'s connections</h2>
          <p className={s.connectText}>Go on to the next step to choose it in {agent.name}.</p>
          <Button variant="ghost" size="sm" onClick={go}>Add it again</Button>
        </>
      ) : (
        <>
          <h2 className={s.connectTitle}>{step.title}</h2>
          <div className={cx(s.connectText, s.rich)}><RichText nodes={step.body} /></div>
          <Button variant="primary" icon="check-circle" onClick={go}>Add Eggie to {agent.name}</Button>
        </>
      )}
    </div>
  );
}

type Check = "idle" | "checking" | "notYet" | "unreachable";

const CHECK_TEXT: Record<"notYet" | "unreachable", (name: string) => string> = {
  notYet: (name) => `Not connected yet. Finish the last step in ${name}, then check again.`,
  unreachable: () => "Couldn't reach your kitchen just now. Check again in a moment.",
};

function Steps({ agent, guide: shown, connect, welcome, byHand, setByHand }: {
  agent: Agent;
  guide: Guide;
  connect: Connect | undefined;
  welcome: boolean;
  byHand: boolean;
  setByHand: (byHand: boolean) => void;
}) {
  const offer = useConnectOffer(agent);
  const { steps, auto } = guideSteps(shown, offer !== undefined, byHand);
  // The automatic list needs nothing copied, so it drops the card.
  const guide = { ...shown, steps, card: auto ? null : shown.card };
  const [active, setActiveStep] = useState(() => (auto ? firstStep(steps, offer!.added) : 0));
  const [check, setCheck] = useState<Check>("idle");
  const navigate = useNavigate();
  const { finish } = useOnboarding();
  const status = useAgentStatus(agent.id);
  const connected = status.data?.agents[agent.id]?.connected === true;
  const rows = cardRows(guide, connect);
  const step = guide.steps[active];
  const last = active === guide.steps.length - 1;
  const setActive = (index: number) => {
    setActiveStep(index);
    setCheck("idle");
  };

  const checkConnection = async () => {
    setCheck("checking");
    const result = await status.refetch();
    if (result.data?.agents[agent.id]?.connected) finish(agent.name);
    else setCheck(result.isError ? "unreachable" : "notYet");
  };

  let primary;
  if (welcome && connected) primary = <Button variant="primary" onClick={() => finish(agent.name)}>Continue</Button>;
  else if (welcome && last)
    primary = (
      <Button variant="primary" disabled={check === "checking"} onClick={checkConnection}>
        {check === "checking" ? "Checking…" : "Check the connection"}
      </Button>
    );
  else
    primary = (
      <Button variant="primary" iconRight={last ? undefined : "chevron-right"} onClick={() => (last ? navigate("/") : setActive(active + 1))}>
        {last ? "Done" : "Next"}
      </Button>
    );
  const problem = welcome && !connected && (check === "notYet" || check === "unreachable") ? CHECK_TEXT[check](agent.name) : null;

  return (
    <div className={s.guide}>
      <div className={s.side}>
        {guide.warning && (
          <aside aria-label="Before you start">
            <Notice tone="warning" className={s.rich}>
              <RichText nodes={guide.warning} />
            </Notice>
          </aside>
        )}
        <ol className={s.steps}>
          {guide.steps.map((item, index) => (
            <li key={index} className={cx(s.step, index === active && s.stepActive)}>
              <button
                type="button"
                className={s.stepHead}
                aria-current={index === active ? "step" : undefined}
                onClick={() => setActive(index)}
              >
                <span className={s.stepDot}>{index + 1}</span>
                <span className={s.stepTitle}>{item.title}</span>
              </button>
              {/* Outside the button: a list is not allowed inside one. */}
              {index === active && <div className={cx(s.stepBody, s.rich)}><RichText nodes={item.body} /></div>}
            </li>
          ))}
        </ol>
        {offer && shown.auto && (
          <button type="button" className={s.otherWay} onClick={() => setByHand(!byHand)}>
            {auto ? "Set it up by hand instead" : "Let Eggie add the connection"}
          </button>
        )}
        <AgentStatusLine agent={agent} />
        {rows && (
          <section className={s.key} aria-label="Connection details">
            <h2 className={s.keyLabel}>Your kitchen's key</h2>
            {rows.map((row) => (
              <div key={row.label} className={s.keyRow}>
                <span className={s.keyText}>
                  <span className={s.keyName}>
                    {row.label}
                    {row.hint && <Hint label={row.label} text={row.hint} />}
                  </span>
                  <span className={cx(s.keyValue, !row.copy && s.keyUnknown)} title={row.value}>{row.value}</span>
                </span>
                {row.copy && <CopyButton className={s.copy} text={row.value} />}
              </div>
            ))}
            <p className={s.keyNote}>{agent.name} asks for these once. If it can't connect, ask us for help.</p>
          </section>
        )}
      </div>
      <div className={s.main}>
        <Card className={s.frame}>
          {step.action === "connect" && offer
            ? <ConnectCard agent={agent} step={step} offer={offer} />
            : <MediaSlider key={active} media={step.media} alt={step.alt} />}
        </Card>
        <div className={s.nav}>
          {/* Mounted before it has text, so screen readers announce the message. */}
          <span className={s.checkProblem} role="status">{problem}</span>
          {!problem && <span className={s.count}>Step {active + 1} of {guide.steps.length}</span>}
          {/* Back and the primary wrap as one pair, so neither is left alone on a line. */}
          <div className={s.navActions}>
            {welcome && <SkipButton />}
            <div className={s.navPair}>
              <Button variant="secondary" disabled={active === 0} onClick={() => setActive(active - 1)}>Back</Button>
              {primary}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
