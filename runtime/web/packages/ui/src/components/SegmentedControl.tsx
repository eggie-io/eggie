import { useRef, type KeyboardEvent } from "react";
import { cx } from "../cx";
import s from "./SegmentedControl.module.css";

const STEP: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };

export function SegmentedControl<T extends string>({
  label,
  options,
  value,
  onChange,
  className,
}: {
  label: string;
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
}) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const current = options.findIndex((option) => option.value === value);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const last = options.length - 1;
    const from = Math.max(current, 0);
    let next: number;
    if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    else if (Object.hasOwn(STEP, event.key)) next = (from + STEP[event.key] + options.length) % options.length;
    else return;
    event.preventDefault();
    onChange(options[next].value);
    buttons.current[next]?.focus();
  };

  return (
    <div role="radiogroup" aria-label={label} className={cx(s.group, className)} onKeyDown={onKeyDown}>
      {options.map((option, i) => {
        const checked = i === current;
        return (
          <button
            key={option.value}
            ref={(el) => { buttons.current[i] = el; }}
            type="button"
            role="radio"
            aria-checked={checked}
            // Roving tabindex: Tab reaches the group once; with nothing chosen it lands on the first.
            tabIndex={checked || (current < 0 && i === 0) ? 0 : -1}
            className={cx(s.segment, checked && s.checked)}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
