import { useState } from "react";
import { actionError } from "./copy";
import { useSetSecret } from "./queries";
import { baseName, fileToBase64 } from "./secrets";

export type SecretKind = "text" | "file";

// Pasted keys often carry a stray space or tab at the ends; newlines belong to PEM bodies.
export const trimValue = (v: string) => v.replace(/^[ \t]+|[ \t]+$/g, "");

// Saves a typed value, or the picked file as base64 with its bare name.
export function useSecretSave(id: string, name: string, kind: SecretKind, onSaved: () => void) {
  const save = useSetSecret(id);
  const [value, setValue] = useState("");
  const [picked, setPicked] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const ready = kind === "file" ? picked !== null : trimValue(value) !== "";
  const submit = async () => {
    setProblem(null);
    try {
      await save.mutateAsync(
        kind === "file" && picked
          ? { name, kind, filename: baseName(picked.name), value: await fileToBase64(picked) }
          : { name, value: trimValue(value) },
      );
      setValue("");
      setPicked(null);
      onSaved();
    } catch (error) {
      setProblem(actionError(error));
    }
  };
  const reset = () => { setValue(""); setPicked(null); setProblem(null); };
  return { value, setValue, setPicked, problem, ready, pending: save.isPending, submit, reset };
}
