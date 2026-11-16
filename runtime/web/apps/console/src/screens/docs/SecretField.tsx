import { useState } from "react";
import { Button, Notice, TextField } from "@eggie/ui";
import { FilePicker } from "../../components/FilePicker";
import { actionError } from "../../projects/copy";
import { relativeTime } from "../../projects/format";
import { useSecrets, useSetSecret } from "../../projects/queries";
import { baseName, fieldState, fileToBase64 } from "../../projects/secrets";
import s from "./DocsPage.module.css";

// Same trim as the Secrets page: pasted keys carry stray spaces; newlines belong to PEM bodies.
const trimValue = (v: string) => v.replace(/^[ \t]+|[ \t]+$/g, "");

export function SecretField({ id, name, file }: { id: string; name: string; file: boolean }) {
  const { data } = useSecrets(id);
  const save = useSetSecret(id);
  const [replacing, setReplacing] = useState(false);
  const [value, setValue] = useState("");
  const [picked, setPicked] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const state = fieldState(data, name, file);
  const ready = file ? picked !== null : trimValue(value) !== "";

  const submit = async () => {
    setProblem(null);
    try {
      const body = file && picked
        ? { name, kind: "file" as const, filename: baseName(picked.name), value: await fileToBase64(picked) }
        : { name, value: trimValue(value) };
      await save.mutateAsync(body);
      setValue("");
      setPicked(null);
      setReplacing(false);
    } catch (error) {
      setProblem(actionError(error));
    }
  };

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
              ? <FilePicker label="Key file" onPick={setPicked} />
              : <TextField label="Value" secret value={value} onChange={setValue} />}
          </div>
          <div className={s.secretActions}>
            <Button size="sm" variant="primary" disabled={!ready || save.isPending} onClick={() => void submit()}>Save</Button>
            {replacing && <Button size="sm" variant="ghost" onClick={() => { setReplacing(false); setProblem(null); }}>Cancel</Button>}
          </div>
        </>
      )}
      {problem && <Notice tone="danger" className={s.secretError}>{problem}</Notice>}
    </div>
  );
}
