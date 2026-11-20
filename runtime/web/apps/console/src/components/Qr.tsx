import { qrModules } from "./qr";

// Drawn as one SVG path of dark modules, so nothing is injected as markup.
export function Qr({ value, label, size = 184 }: { value: string; label: string; size?: number }) {
  const qr = qrModules(value);
  if (!qr) return null;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${qr.box} ${qr.box}`} role="img" aria-label={label} shapeRendering="crispEdges">
      <rect width={qr.box} height={qr.box} fill="#fff" />
      <path d={qr.d} fill="#000" />
    </svg>
  );
}
