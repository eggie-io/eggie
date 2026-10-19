import { useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router";
import { Button, Card, Notice, TextField } from "@eggie/ui";
import { actionError } from "../../projects/copy";
import { useDeleteSecret, useLifecycle, useSecrets, useSetSecret } from "../../projects/queries";
import { nameProblem } from "../../projects/secrets";
import { TitleRow } from "../project/TitleRow";
import s from "./SecretsPage.module.css";

// Pasted keys often carry a stray space or tab at the ends; newlines belong to PEM bodies.
const trimValue = (v: string) => v.replace(/^[ \t]+|[ \t]+$/g, "");

function RequestedRow({ id, name, hint }: { id: string; name: string; hint: string }) {
  const save = useSetSecret(id);
  const dismiss = useDeleteSecret(id);
  const [value, setValue] = useState("");
  return (
    <li className={s.row}>
      <span className={s.name}>{name}</span>
      <div className={s.field}>
        <p className={s.hint}>{hint}</p>
        <TextField label="Value" secret value={value} onChange={setValue} />
      </div>
      <div className={s.actions}>
        <Button disabled={trimValue(value) === "" || save.isPending} onClick={() => save.mutate({ name, value: trimValue(value) }, { onSuccess: () => setValue("") })}>Save</Button>
        <Button variant="ghost" disabled={dismiss.isPending} onClick={() => dismiss.mutate(name)}>Dismiss</Button>
      </div>
      {(save.error || dismiss.error) && <Notice tone="danger" className={s.wide}>{actionError(save.error ?? dismiss.error)}</Notice>}
    </li>
  );
}

function StoredRow({ id, name }: { id: string; name: string }) {
  const save = useSetSecret(id);
  const remove = useDeleteSecret(id);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const close = () => { setValue(""); setEditing(false); };
  return (
    <li className={s.row}>
      <span className={s.name}>{name}</span>
      {editing ? (
        <>
          <TextField label="New value" secret autoFocus value={value} onChange={setValue} />
          <div className={s.actions}>
            <Button disabled={trimValue(value) === "" || save.isPending} onClick={() => save.mutate({ name, value: trimValue(value) }, { onSuccess: close })}>Save</Button>
            <Button variant="ghost" onClick={close}>Cancel</Button>
          </div>
        </>
      ) : (
        <>
          <span className={s.masked}>••••••••</span>
          <div className={s.actions}>
            <Button onClick={() => setEditing(true)}>Replace</Button>
            <Button variant="danger" disabled={remove.isPending} onClick={() => remove.mutate(name)}>Delete</Button>
          </div>
        </>
      )}
      {(save.error || remove.error) && <Notice tone="danger" className={s.wide}>{actionError(save.error ?? remove.error)}</Notice>}
    </li>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card as="section">
      <header className={s.cardHead}>
        <h2 className={s.cardTitle}>{title}</h2>
      </header>
      {children}
    </Card>
  );
}

export function SecretsPage() {
  const { id = "" } = useParams();
  const query = useSecrets(id);
  const add = useSetSecret(id);
  const lifecycle = useLifecycle(id);
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [tried, setTried] = useState(false);

  const projectUrl = `/p/${encodeURIComponent(id)}`;
  const lead = (
    <span className={s.lead}>
      Keys and passwords for outside services, like Stripe, OpenAI or your mail provider. Eggie hands them to {id} as
      environment variables when it starts; they override the same name in .env and never land in the project's files
      or git. Saved values can't be shown again, only replaced.
    </span>
  );
  if (!query.data) {
    return (
      <section className={s.page}>
        <TitleRow title="Secrets" />
        {query.isError ? <Notice tone="danger">{actionError(query.error)}</Notice> : <p className={s.muted}>Opening the spice drawer…</p>}
      </section>
    );
  }
  const data = query.data;
  const taken = data.secrets.map((x) => x.name);
  const problem = nameProblem(name, taken);

  return (
    <section className={s.page}>
      <TitleRow title="Secrets" sub={lead} />
      {data.restart_needed && (
        <Notice tone="warning">
          Secrets changed — restart {id} to apply them.
          <div className={s.noticeActions}>
            <Button size="sm" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("restart", { onSuccess: () => navigate(projectUrl) })}>
              Restart
            </Button>
          </div>
        </Notice>
      )}
      {lifecycle.error && <Notice tone="danger">{actionError(lifecycle.error)}</Notice>}
      {data.requested.length > 0 && (
        <Section title="Requested">
          <ul className={s.rows}>
            {data.requested.map((r) => <RequestedRow key={r.name} id={id} name={r.name} hint={r.hint} />)}
          </ul>
        </Section>
      )}
      {data.secrets.length > 0 && (
        <Section title="Your secrets">
          <ul className={s.rows}>
            {data.secrets.map((x) => <StoredRow key={x.name} id={id} name={x.name} />)}
          </ul>
        </Section>
      )}
      <Section title="Add a secret">
        <div className={s.form}>
          <TextField label="Name" value={name} onChange={setName} placeholder="OPENAI_API_KEY" error={tried && problem ? problem : undefined} />
          <TextField label="Value" secret value={value} onChange={setValue} />
          <div className={s.formAction}>
            <Button
              variant="primary"
              disabled={add.isPending || trimValue(value) === ""}
              onClick={() => {
                setTried(true);
                if (problem) return;
                add.mutate({ name: name.trim(), value: trimValue(value) }, { onSuccess: () => { setName(""); setValue(""); setTried(false); } });
              }}
            >
              Add
            </Button>
          </div>
          {add.error && <Notice tone="danger" className={s.wide}>{actionError(add.error)}</Notice>}
        </div>
      </Section>
    </section>
  );
}
