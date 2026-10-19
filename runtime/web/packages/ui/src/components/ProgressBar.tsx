import s from "./ProgressBar.module.css";

export function ProgressBar({ value, label }: { value?: number; label: string }) {
  if (value === undefined) {
    return (
      <div className={s.track} role="progressbar" aria-label={label}>
        <div className={s.sweep} />
      </div>
    );
  }
  const percent = Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <div className={s.track} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
      <div className={s.fill} style={{ width: `${percent}%` }} />
    </div>
  );
}
