import { useState, type ReactNode } from "react";
import {
  Badge,
  BrandMark,
  Button,
  Card,
  Choice,
  Collapsible,
  Egg,
  Icon,
  IconButton,
  Modal,
  Notice,
  ProgressBar,
  PromptCard,
  SegmentedControl,
  StateBadge,
  StatusDot,
  TextField,
  cx,
} from "@eggie/ui";
import s from "./Kit.module.css";

const PROMPT = `I uploaded a database dump to data/orders-dump.sql in this
project. Please import it into the project's database using
the credentials already configured, then tell me which
tables landed and roughly how many rows each one has.`;

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className={s.section}>
      <h3 className={s.label}>{title}</h3>
      <div className={s.items}>{children}</div>
    </section>
  );
}

function Column({ theme }: { theme: "light" | "dark" }) {
  const [open, setOpen] = useState(false);
  const [wide, setWide] = useState(false);
  const [secret, setSecret] = useState("sk-live-4f9a");
  const [mode, setMode] = useState("merge");
  const [focus, setFocus] = useState<"full" | "start" | "share">("full");
  return (
    <div className={cx(`om-theme-${theme}`, s.column)}>
      <h2 className={s.heading}><BrandMark size={22} /> {theme}</h2>
      <Section title="Buttons">
        <Button variant="primary" icon="plus">New project</Button>
        <Button variant="secondary">Already did</Button>
        <Button variant="ghost">Stop</Button>
        <Button variant="danger" icon="trash">Delete project</Button>
        <Button disabled>Not yet</Button>
      </Section>
      <Section title="Button sizes and icons">
        <Button variant="primary" size="sm">Small</Button>
        <Button variant="primary">Medium</Button>
        <Button variant="primary" size="lg">Try again</Button>
        <Button variant="secondary" iconRight="chevron-down">This computer</Button>
        <Button variant="ghost" size="sm" icon="chevron-left">Agents</Button>
      </Section>
      <Section title="Icon buttons">
        <IconButton icon="more" label="More" />
        <IconButton icon="refresh" label="Refresh" variant="secondary" />
        <IconButton icon="chevron-left" label="Back" size="sm" />
        <IconButton icon="x" label="Close" size="sm" variant="secondary" />
        <IconButton icon="copy" label="Copy" disabled />
      </Section>
      <Section title="Badges">
        <Badge>Neutral</Badge>
        <Badge tone="success" icon="check-circle">Running</Badge>
        <Badge tone="warning">Not on GitHub</Badge>
        <Badge tone="danger" icon="x-circle">Didn’t start</Badge>
        <Badge tone="info">New</Badge>
        <Badge tone="gold">Yours</Badge>
        <Badge tone="accent">Selected</Badge>
        <Badge tone="outline">Stopped</Badge>
      </Section>
      <Section title="Status dots">
        <StatusDot status="running" />
        <StatusDot status="starting" />
        <StatusDot status="stopped" />
        <StatusDot status="failed" />
        <StatusDot status="waiting" />
        <StatusDot status="degraded" />
        <StatusDot status="running" label="Open" />
      </Section>
      <Section title="State badges">
        <StateBadge state="running" />
        <StateBadge state="stopped" />
        <StateBadge state="starting" />
        <StateBadge state="wrong" />
        <StateBadge state="waiting" />
        <StateBadge state="setup" />
      </Section>
      <Section title="Notices">
        <Notice className={s.wide}>These addresses only work on this computer. Nothing is out on the internet.</Notice>
        <Notice className={s.wide} icon="folder">Already have a folder on your computer? The desktop app carries it in for you.</Notice>
        <Notice className={s.wide} tone="success" title="Imported">recipe-box is ready to start.</Notice>
        <Notice className={s.wide} tone="warning" title="Storage is 82% full">Writes will fail once the volume is full.</Notice>
        <Notice className={s.wide} tone="danger" title="Couldn’t save the secret">The kitchen didn’t answer. Try again in a moment.</Notice>
      </Section>
      <Section title="Text fields">
        <div className={s.wide}>
          <TextField label="Name" value="weekend shop" onChange={() => {}} hint={<>It’ll be called <strong>weekend-shop</strong></>} />
        </div>
        <div className={s.wide}>
          <TextField label="Name" value="recipe-box" onChange={() => {}} error="There’s already a project called recipe-box." />
        </div>
        <div className={s.wide}>
          <TextField label="Value" value={secret} onChange={setSecret} secret placeholder="Paste the value" />
        </div>
      </Section>
      <Section title="Choices">
        <div className={s.stack}>
          <Choice type="radio" name={`mode-${theme}`} checked={mode === "merge"} onChange={() => setMode("merge")} label="Add to what’s there" description="Files with the same name are replaced." />
          <Choice type="radio" name={`mode-${theme}`} checked={mode === "replace"} onChange={() => setMode("replace")} label="Replace everything" />
          <Choice type="checkbox" defaultChecked label="Keep the original" />
          <Choice type="checkbox" label="Not available" disabled />
        </div>
      </Section>
      <Section title="Segmented control">
        <SegmentedControl
          label="Analyze focus"
          options={[
            { value: "full", label: "Full once-over" },
            { value: "start", label: "Why won't it start?" },
            { value: "share", label: "Safe to share?" },
          ]}
          value={focus}
          onChange={setFocus}
        />
      </Section>
      <Section title="Cards">
        <Card className={s.cardDemo}>A card: paper, a hairline and a small shadow.</Card>
      </Section>
      <Section title="Progress">
        <div className={s.wide}><ProgressBar value={0.64} label="Uploading" /></div>
        <div className={s.wide}><ProgressBar label="Starting" /></div>
      </Section>
      <Section title="Collapsible">
        <Collapsible summary="What Eggie looks for">A compose file at the top of the folder.</Collapsible>
        <div className={s.wide}>
          <Collapsible boxed summary="The raw details" aside="for your coding agent, or for us">
            <code>container exited with code 1</code>
          </Collapsible>
        </div>
      </Section>
      <Section title="Prompt card">
        <div className={s.wide}><PromptCard prompt={PROMPT} aside="live — give it a click" /></div>
      </Section>
      <Section title="Egg and icons">
        <Egg size={40} bob />
        <Egg size={40} dim />
        <Egg size={40} tone="cold" />
        <Icon name="rocket" />
        <Icon name="folder" size="md" />
        <Icon name="key" size="md" label="Secret" />
      </Section>
      <Section title="Dialogs">
        <Button onClick={() => setOpen(true)}>Open a dialog</Button>
        <Button onClick={() => setWide(true)}>Open a wide dialog</Button>
        <Modal
          open={open}
          onClose={() => setOpen(false)}
          title="Throw out recipe-box?"
          footer={
            <>
              <Button variant="ghost" onClick={() => setOpen(false)}>Keep it</Button>
              <Button variant="danger" onClick={() => setOpen(false)}>Throw it out</Button>
            </>
          }
        >
          <p className={s.body}>Esc, the backdrop, the close button or either button closes it.</p>
          <TextField label="Type recipe-box to confirm" value="" onChange={() => {}} />
        </Modal>
        <Modal open={wide} onClose={() => setWide(false)} title="Ask for a once-over" wide>
          <PromptCard prompt={PROMPT} />
        </Modal>
      </Section>
    </div>
  );
}

export function Kit() {
  return (
    <div className={s.columns}>
      <Column theme="light" />
      <Column theme="dark" />
    </div>
  );
}
