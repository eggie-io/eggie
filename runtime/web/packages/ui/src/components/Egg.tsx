import { cx } from "../cx";
import s from "./Egg.module.css";

export function Egg({
  tone = "yolk",
  size = 22,
  bob = false,
  dim = false,
}: {
  tone?: "yolk" | "cold";
  size?: number;
  bob?: boolean;
  dim?: boolean;
}) {
  return (
    <svg className={cx(s.egg, bob && s.bob)} width={size} height={size} viewBox="0 0 40 40" fill="none" aria-hidden="true">
      <ellipse className={s.white} cx="20" cy="21" rx="16" ry="13" />
      <circle className={cx(tone === "cold" ? s.cold : s.yolk, dim && s.dim)} cx="20" cy="20" r="7" />
    </svg>
  );
}
