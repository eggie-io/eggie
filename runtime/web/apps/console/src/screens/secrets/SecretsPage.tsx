import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { Button, Notice, TextField } from "@eggie/ui";
import { actionError } from "../../projects/copy";
import { useDeleteSecret, useImportDotenv, useLifecycle, useSecrets, useSetSecret } from "../../projects/queries";
import { nameProblem } from "../../projects/secrets";
import page from "../project/ProjectPage.module.css";
import s from "./SecretsPage.module.css";

function ValueRow({ id, name, missing }: { id: string; name: string; missing: boolean }) {
  const save = useSetSecret(id);
  const remove = useDeleteSecret(id);
  const [editing, setEditing] = useState(missing);
  const [value, setValue] = useState("");
  const submit = () =>
    save.mutate({ name, value }, { onSuccess: () => { setValue(""); setEditing(missing); } });
  return (
    <div className={s.row}>
      <span className={s.name}>{name}</span>
      {editing ? (
        <>
          <div className={s.grow}>
            <TextField label={missing ? "Needs a value" : "New value"} type="password" value={value} onChange={setValue} />
          </div>
          <Button variant="primary" disabled={value === "" || save.isPending} onClick={submit}>Save</Button>
          {!missing && <Button onClick={() => { setValue(""); setEditing(false); }}>Cancel</Button>}
        </>
      ) : (
        <>
          <span className={`${s.masked} ${s.grow}`}>••••••••</span>
          <Button onClick={() => setEditing(true)}>Edit</Button>
          <Button variant="danger" disabled={remove.isPending} onClick={() => remove.mutate(name)}>Delete</Button>
        </>
      )}
      {missing && <span className={s.missing}>missing</span>}
      {(save.error || remove.error) && <Notice>{actionError(save.error ?? remove.error)}</Notice>}
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
        Keys and passwords {id} needs. Eggie hands them to the app as environment variables when it starts; they never
        go into the project's files. A saved value can't be shown again — only replaced.
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
      <div className={s.rows}>
        {data.missing.map((n) => <ValueRow key={`m-${n}`} id={id} name={n} missing />)}
        {data.secrets.map((x) => <ValueRow key={`s-${x.name}`} id={id} name={x.name} missing={false} />)}
      </div>
      <h2 className={page.big}>Add a secret</h2>
      <div className={s.row}>
        <div className={s.grow}>
          <TextField label="Name" value={name} onChange={setName} placeholder="OPENAI_API_KEY" error={tried && problem ? problem : undefined} />
        </div>
        <div className={s.grow}>
          <TextField label="Value" type="password" value={value} onChange={setValue} />
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
