import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { Button, Notice, TextField } from "@eggie/ui";
import { actionError } from "../../projects/copy";
import { useDeleteSecret, useLifecycle, useSecrets, useSetSecret } from "../../projects/queries";
import { nameProblem } from "../../projects/secrets";
import page from "../project/ProjectPage.module.css";
import s from "./SecretsPage.module.css";

function RequestedRow({ id, name, hint }: { id: string; name: string; hint: string }) {
  const save = useSetSecret(id);
  const dismiss = useDeleteSecret(id);
  const [value, setValue] = useState("");
  return (
    <div className={s.row}>
      <span className={s.name}>{name}</span>
      <div className={s.grow}>
        <p className={page.muted}>{hint}</p>
        <TextField label="Value" secret value={value} onChange={setValue} />
      </div>
      <Button variant="primary" disabled={value === "" || save.isPending} onClick={() => save.mutate({ name, value }, { onSuccess: () => setValue("") })}>Save</Button>
      <Button disabled={dismiss.isPending} onClick={() => dismiss.mutate(name)}>Dismiss</Button>
      {(save.error || dismiss.error) && <Notice>{actionError(save.error ?? dismiss.error)}</Notice>}
    </div>
  );
}

function StoredRow({ id, name }: { id: string; name: string }) {
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
          <Button onClick={() => setEditing(true)}>Replace</Button>
          <Button variant="danger" disabled={remove.isPending} onClick={() => remove.mutate(name)}>Delete</Button>
        </>
      )}
      {(save.error || remove.error) && <Notice>{actionError(save.error ?? remove.error)}</Notice>}
    </div>
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
        Keys and passwords for outside services, like Stripe, OpenAI or your mail provider. Eggie hands them to {id} as
        environment variables when it starts; they override the same name in .env and never land in the project's files
        or git. Saved values can't be shown again, only replaced.
      </p>
      {data.restart_needed && (
        <Notice>
          Secrets changed — restart {id} to apply them.{" "}
          <Button disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("restart", { onSuccess: () => navigate(projectUrl) })}>
            Restart
          </Button>
        </Notice>
      )}
      {data.requested.length > 0 && (
        <>
          <h2 className={page.big}>Requested</h2>
          <div className={s.rows}>
            {data.requested.map((r) => <RequestedRow key={r.name} id={id} name={r.name} hint={r.hint} />)}
          </div>
        </>
      )}
      {data.secrets.length > 0 && (
        <>
          <h2 className={page.big}>Your secrets</h2>
          <div className={s.rows}>
            {data.secrets.map((x) => <StoredRow key={x.name} id={id} name={x.name} />)}
          </div>
        </>
      )}
      <h2 className={page.big}>Add a secret</h2>
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
