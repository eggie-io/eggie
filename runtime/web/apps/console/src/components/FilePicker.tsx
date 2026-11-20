import { useId } from "react";
import s from "./FilePicker.module.css";

export function FilePicker({ label, onPick }: { label: string; onPick: (file: File | null) => void }) {
  const id = useId();
  return (
    <div className={s.field}>
      <label htmlFor={id} className={s.label}>{label}</label>
      <input id={id} type="file" className={s.input} onChange={(e) => onPick(e.target.files?.[0] ?? null)} />
    </div>
  );
}
