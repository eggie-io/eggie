import { useEffect, useId, useRef, type ReactNode } from "react";
import { cx } from "../cx";
import { IconButton } from "./IconButton";
import s from "./Modal.module.css";

// A native <dialog> opened with showModal(): the browser makes the rest of the
// page inert, traps focus and hands it back to the opener on close.
export function Modal({
  open,
  onClose,
  title,
  footer,
  wide = false,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  footer?: ReactNode;
  wide?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className={cx(s.dialog, wide && s.wide)}
      aria-labelledby={titleId}
      onCancel={(event) => {
        // Esc: let the parent's `open` stay the only source of truth.
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        // The panel fills the dialog, so a click landing on the dialog itself is the backdrop.
        if (event.target === event.currentTarget) onClose();
      }}
      onClose={() => {
        // Browser-initiated close (e.g., form method="dialog"): only notify if open was true.
        if (open) onClose();
      }}
    >
      <div className={s.panel}>
        <div className={s.head}>
          <h2 id={titleId} className={s.title}>{title}</h2>
        </div>
        <div className={s.body}>{children}</div>
        {footer && <div className={s.foot}>{footer}</div>}
        {/* Last in the DOM so showModal() still focuses the first control of the content. */}
        <IconButton icon="x" label="Close" size="sm" className={s.close} onClick={onClose} />
      </div>
    </dialog>
  );
}
