import styles from "./BrandMark.module.css";

// The design system's split-egg logo; it takes the colour of the text around it.
export function BrandMark({ size = 18 }: { size?: number }) {
  return (
    <svg aria-hidden="true" className={styles.mark} viewBox="0 0 110 133" width={(size * 110) / 133} height={size}>
      <path d="M64 5C80 5 90 17 97 34C103 49 105 62 105 74C105 106 82 128 54 128C25 128 5 108 5 86C5 64 16 43 30 28C41 16 52 5 64 5Z" />
      <path d="M76.9 8.1C80 9.7 82.9 12 85.5 14.7C88.9 18.4 92.6 30 92 44C91 70 74 84 54 89C38 93 20 91 6 86" />
    </svg>
  );
}
