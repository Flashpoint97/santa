import type { ReactNode } from "react";
import styles from "./PaperCard.module.css";
import Stamp from "./Stamp";
import { cx } from "./cx";

type Props = {
  /** Small uppercase mono label in the top-left corner. */
  label?: string;
  /** Show the red stamp in the top-right corner. */
  stamp?: boolean;
  /** "invitation" (default), "note" (handwritten, ruled paper) or "celebration" (after the event). */
  variant?: "invitation" | "note" | "celebration";
  className?: string;
  children: ReactNode;
};

/** The cream paper card with the hard shadow. */
export default function PaperCard({
  label,
  stamp = false,
  variant = "invitation",
  className,
  children,
}: Props) {
  return (
    <div className={cx(styles.card, styles[variant], className)}>
      {(label || stamp) && (
        <div className={styles.top}>
          {label ? <span className="label">{label}</span> : <span />}
          {stamp && <Stamp />}
        </div>
      )}
      {children}
    </div>
  );
}
