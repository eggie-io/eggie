import { useId, useState } from "react";
import { Button } from "@eggie/ui";
import type { Asking } from "../../actions/types";
import s from "./ActionCard.module.css";

// The typed value lives only here: it reaches the API through onSend and is dropped at once.
export function QuestionField({
  asking,
  onSend,
  sending = false,
  error,
}: {
  asking: Asking;
  onSend: (value: string) => void;
  sending?: boolean;
  error?: string | null;
}) {
  const id = useId();
  const [value, setValue] = useState("");
  const secret = asking.type === "secret";
  const send = () => {
    if (!value || sending) return;
    onSend(value);
    setValue("");
  };
  return (
    <form className={s.question} onSubmit={(event) => { event.preventDefault(); send(); }}>
      <label htmlFor={id} className={s.questionLabel}>{asking.label}</label>
      <div className={s.questionRow}>
        <input
          id={id}
          className={s.questionInput}
          type={secret ? "password" : "text"}
          // new-password keeps password managers from offering to save an Apple ID password.
          autoComplete={secret ? "new-password" : "off"}
          spellCheck={false}
          autoFocus
          value={value}
          onChange={(event) => setValue(event.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
        />
        <Button type="submit" variant="primary" disabled={!value || sending}>Send</Button>
      </div>
      {error && <p id={`${id}-error`} className={s.error} role="alert">{error}</p>}
    </form>
  );
}
