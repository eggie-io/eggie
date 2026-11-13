import qrcode from "qrcode-generator";

const QUIET = 4;

// One SVG path of dark modules; null when the text is too long for any QR code.
export function qrModules(value: string): { d: string; box: number } | null {
  const qr = qrcode(0, "M");
  try {
    qr.addData(value);
    qr.make();
  } catch {
    return null;
  }
  const count = qr.getModuleCount();
  let d = "";
  for (let row = 0; row < count; row++) {
    for (let col = 0; col < count; col++) {
      if (qr.isDark(row, col)) d += `M${col + QUIET} ${row + QUIET}h1v1h-1z`;
    }
  }
  return { d, box: count + QUIET * 2 };
}
