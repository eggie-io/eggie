import { ICON_PATHS, type IconName } from "../icons";

type Props = { name: IconName; size?: "sm" | "md"; label?: string; className?: string };

export function Icon({ name, size = "sm", label, className }: Props) {
  const px = size === "md" ? 20 : 16;
  return (
    <svg
      viewBox="0 0 24 24"
      width={px}
      height={px}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={{ flex: "none" }}
      {...(label ? { role: "img", "aria-label": label } : { "aria-hidden": true })}
    >
      {ICON_PATHS[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}
