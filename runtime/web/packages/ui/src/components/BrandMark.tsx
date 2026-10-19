import styles from "./BrandMark.module.css";

// Sized through font-size so the inset shadow scales with the egg.
export function BrandMark({ size = 18 }: { size?: number }) {
  return <span aria-hidden="true" className={styles.mark} style={{ fontSize: size }} />;
}
