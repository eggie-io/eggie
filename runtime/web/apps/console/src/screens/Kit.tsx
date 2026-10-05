import { useState, type ReactNode } from "react";
import {
  Button,
  Collapsible,
  Egg,
  Modal,
  Notice,
  ProgressBar,
  PromptCard,
  RowCard,
  StateBadge,
  SyncMarker,
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
  return (
    <div className={cx(`om-theme-${theme}`, s.column)}>
      <h2 className={s.heading}>{theme}</h2>
      <Section title="Buttons">
        <Button variant="primary">New project</Button>
        <Button variant="primary" size="lg">Try again</Button>
        <Button variant="secondary">Already did</Button>
        <Button variant="quiet">Stop</Button>
        <Button variant="danger">Take a look</Button>
        <Button disabled>Not yet</Button>
      </Section>
      <Section title="Text field">
        <div className={s.wide}>
          <TextField label="Name" value="weekend shop" onChange={() => {}} hint={<>It'll be called <strong>weekend-shop</strong></>} />
        </div>
        <div className={s.wide}>
          <TextField label="Name" value="recipe-box" onChange={() => {}} error="There's already a project called recipe-box." />
        </div>
      </Section>
      <Section title="State badges">
        <StateBadge state="running" />
        <StateBadge state="stopped" />
        <StateBadge state="starting" />
        <StateBadge state="wrong" />
      </Section>
      <Section title="Sync marker (hidden in the app until sync exists)">
        <SyncMarker state="synced" />
        <SyncMarker state="offline" />
      </Section>
      <Section title="Egg">
        <Egg size={40} bob />
        <Egg size={40} tone="cold" />
      </Section>
      <Section title="Notice">
        <Notice>These addresses only work on this computer. Nothing is out on the internet.</Notice>
        <Notice icon="folder">Already have a folder on your computer? The desktop app carries it in for you.</Notice>
      </Section>
      <Section title="Row cards">
        <RowCard className={s.wide}>plain</RowCard>
        <RowCard className={s.wide} accent="attention">attention</RowCard>
        <RowCard className={s.wide} accent="trouble">trouble</RowCard>
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
      <Section title="Modal">
        <Button onClick={() => setOpen(true)}>Open a modal</Button>
        <Modal open={open} onClose={() => setOpen(false)} title="Delete recipe-box?">
          <p className={s.body}>Esc, the backdrop or the button closes it.</p>
          <Button variant="danger" onClick={() => setOpen(false)}>Close</Button>
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
