import { createPortal } from "react-dom";
import { Egg } from "@eggie/ui";
import s from "./StatusScreen.module.css";

// Portalled out of the header, whose stacking context would otherwise clip it.
export function UpdatingOverlay() {
  return createPortal(
    <div className={s.overlay} role="alertdialog" aria-modal="true" aria-labelledby="updating-title">
      <div className={s.center}>
        <Egg size={96} bob />
        <div className={s.text}>
          <h1 id="updating-title" className={s.title}>Updating Eggie…</h1>
          <p className={s.lead}>This takes about a minute. Your projects keep running.</p>
        </div>
      </div>
    </div>,
    document.body,
  );
}
