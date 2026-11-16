import type { ReactNode } from "react";
import styles from "./Envelope.module.css";

type Props = {
  /** The one small link on the right of the header (e.g. the logout link). */
  action?: ReactNode;
  children: ReactNode;
};

/** The airmail envelope every screen sits in: striped border, brand + one link, a thin rule. */
export default function Envelope({ action, children }: Props) {
  return (
    <div className={styles.envelope}>
      <header className={styles.header}>
        <span className={styles.brand}>Tajni Djed Mraz</span>
        {action}
      </header>
      <main>{children}</main>
    </div>
  );
}
