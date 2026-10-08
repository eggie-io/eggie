import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { Button, Collapsible, Notice, TextField } from "@eggie/ui";
import { actionError } from "../../projects/copy";
import { useDeleteSecret, useImportDotenv, useLifecycle, useSecrets, useSetSecret } from "../../projects/queries";
import { nameProblem } from "../../projects/secrets";
import page from "../project/ProjectPage.module.css";
import s from "./SecretsPage.module.css";

function MissingRow({ id, name }: { id: string; name: string }) {
  const save = useSetSecret(id);
  const [value, setValue] = useState("");
  return (
    <div className={s.row}>
      <span className={s.name}>{name}</span>
      <div className={s.grow}>
        <TextField label="Value" secret value={value} onChange={setValue} />
      </div>
      <Button variant="primary" disabled={value === "" || save.isPending} onClick={() => save.mutate({ name, value }, { onSuccess: () => setValue("") })}>
        Save
      </Button>
      {save.error && <Notice>{actionError(save.error)}</Notice>}
    </div>
  );
}

function StoredRow({ id, name, resets }: { id: string; name: string; resets: boolean }) {
  const save = useSetSecret(id);
  const remove = useDeleteSecret(id);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const close = () => { setValue(""); setEditing(false); };
  return (
    <div className={s.row}>
      <span className={s.name}>{name}</span>
      {editing ? (
        <>
          <div className={s.grow}>
            <TextField label="New value" secret autoFocus value={value} onChange={setValue} />
          </div>
          <Button variant="primary" disabled={value === "" || save.isPending} onClick={() => save.mutate({ name, value }, { onSuccess: close })}>Save</Button>
          <Button onClick={close}>Cancel</Button>
        </>
      ) : (
        <>
          <span className={`${s.masked} ${s.grow}`}>••••••••</span>
          <Button onClick={() => setEditing(true)}>Edit</Button>
          <Button variant="danger" disabled={remove.isPending} onClick={() => remove.mutate(name)}>
            {resets ? "Reset to default" : "Delete"}
          </Button>
        </>
      )}
      {(save.error || remove.error) && <Notice>{actionError(save.error ?? remove.error)}</Notice>}
    </div>
  );
}

function DefaultRow({ id, name, base, overridden }: { id: string; name: string; base: string; overridden: boolean }) {
  const save = useSetSecret(id);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(base);
  const close = () => { setValue(base); setEditing(false); };
  return (
    <div className={s.row}>
      <span className={s.name}>{name}</span>
      {editing ? (
        <>
          <div className={s.grow}>
            <TextField label="Value" secret autoFocus value={value} onChange={setValue} />
          </div>
          <Button variant="primary" disabled={value === "" || save.isPending} onClick={() => save.mutate({ name, value }, { onSuccess: () => setEditing(false) })}>Save</Button>
          <Button onClick={close}>Cancel</Button>
        </>
      ) : (
        <>
          <span className={`${s.plain} ${s.grow} ${overridden ? s.struck : ""}`}>{base}</span>
          {overridden ? <span className={s.tag}>overridden</span> : <Button onClick={() => setEditing(true)}>Edit</Button>}
        </>
      )}
      {save.error && <Notice>{actionError(save.error)}</Notice>}
    </div>
  );
}

export function SecretsPage() {
  const { id = "" } = useParams();
  const query = useSecrets(id);
  const add = useSetSecret(id);
  const move = useImportDotenv(id);
  const lifecycle = useLifecycle(id);
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [tried, setTried] = useState(false);

  const projectUrl = `/p/${encodeURIComponent(id)}`;
  const back = <Link to={projectUrl} className={page.back}>‹ {id}</Link>;
  if (!query.data) {
    return (
      <section className={s.page}>
        {back}
        {query.isError ? <Notice>{actionError(query.error)}</Notice> : <p className={page.muted}>Opening the spice drawer…</p>}
      </section>
    );
  }
  const data = query.data;
  const taken = data.secrets.map((x) => x.name);
  const problem = nameProblem(name, taken);

  return (
    <section className={s.page}>
      {back}
      <h1 className={page.name}>Secrets</h1>
      <p className={page.lead}>
        Settings and keys {id} needs. Eggie hands them to the app as environment variables when it starts. Values you
        save can't be shown again — only replaced. Defaults come from the project's .env.example.
      </p>
      {data.restart_needed && (
        <Notice>
          Secrets changed — restart {id} to apply them.{" "}
          <Button disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("restart", { onSuccess: () => navigate(projectUrl) })}>
            Restart
          </Button>
        </Notice>
      )}
      {data.dotenv && (data.dotenv.error ? (
        <Notice>This project has a .env file Eggie can't read: {data.dotenv.error}</Notice>
      ) : (
        <Notice icon="folder">
          This project has a .env file with {data.dotenv.names.length} {data.dotenv.names.length === 1 ? "value" : "values"}.
          Move them into secrets so they stay out of the project's files? Eggie leaves the .env file empty.{" "}
          <Button variant="primary" disabled={move.isPending} onClick={() => move.mutate()}>Move into secrets</Button>
        </Notice>
      ))}
      {move.error && <Notice>{actionError(move.error)}</Notice>}
      {data.missing.length > 0 && (
        <>
          <h2 className={page.big}>Needs a value</h2>
          <div className={s.rows}>
            {data.missing.map((n) => <MissingRow key={n} id={id} name={n} />)}
          </div>
        </>
      )}
      {data.secrets.length > 0 && (
        <>
          <h2 className={page.big}>Your values</h2>
          <div className={s.rows}>
            {data.secrets.map((x) => <StoredRow key={x.name} id={id} name={x.name} resets={x.overrides_default} />)}
          </div>
        </>
      )}
      {data.defaults.length > 0 && (
        <Collapsible summary={`Defaults from .env.example (${data.defaults.length})`}>
          <div className={s.rows}>
            {data.defaults.map((d) => <DefaultRow key={d.name} id={id} name={d.name} base={d.value} overridden={d.overridden} />)}
          </div>
        </Collapsible>
      )}
      <h2 className={page.big}>Add a variable</h2>
      <div className={s.row}>
        <div className={s.grow}>
          <TextField label="Name" value={name} onChange={setName} placeholder="OPENAI_API_KEY" error={tried && problem ? problem : undefined} />
        </div>
        <div className={s.grow}>
          <TextField label="Value" secret value={value} onChange={setValue} />
        </div>
        <Button
          variant="primary"
          disabled={add.isPending || value === ""}
          onClick={() => {
            setTried(true);
            if (problem) return;
            add.mutate({ name: name.trim(), value }, { onSuccess: () => { setName(""); setValue(""); setTried(false); } });
          }}
        >
          Add
        </Button>
      </div>
      {add.error && <Notice>{actionError(add.error)}</Notice>}
    </section>
  );
}
