import { useState } from "react";
import { Button, Notice, TextField } from "@eggie/ui";
import { FilePicker } from "../../components/FilePicker";
import { relativeTime } from "../../projects/format";
import { useSecrets } from "../../projects/queries";
import { fieldState } from "../../projects/secrets";
import { useSecretSave } from "../../projects/useSecretSave";
import s from "./DocsPage.module.css";

export function SecretField({ id, name, file }: { id: string; name: string; file: boolean }) {
  const { data } = useSecrets(id);
  const [replacing, setReplacing] = useState(false);
  const save = useSecretSave(id, name, file ? "file" : "text", () => setReplacing(false));
  const state = fieldState(data, name, file);

  const when = state.updatedAt === null ? "" : `saved ${relativeTime(state.updatedAt, Date.now())}`;
  return (
    <div className={s.secret}>
      <span className={s.secretName}>{name}</span>
      {state.saved && !replacing ? (
        <>
          <span className={s.secretSaved}>{file && state.filename ? `${state.filename} · ${when}` : when}</span>
          <Button size="sm" onClick={() => setReplacing(true)}>Replace</Button>
        </>
      ) : (
        <>
          <div className={s.secretInput}>
            {file
              ? <FilePicker label="Key file" onPick={save.setPicked} />
              : <TextField label="Value" secret value={save.value} onChange={save.setValue} />}
          </div>
          <div className={s.secretActions}>
            <Button size="sm" variant="primary" disabled={!save.ready || save.pending} onClick={() => void save.submit()}>Save</Button>
            {replacing && <Button size="sm" variant="ghost" onClick={() => { setReplacing(false); save.reset(); }}>Cancel</Button>}
          </div>
        </>
      )}
      {save.problem && <Notice tone="danger" className={s.secretError}>{save.problem}</Notice>}
    </div>
  );
}
