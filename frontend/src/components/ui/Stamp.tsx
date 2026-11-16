import styles from "./Stamp.module.css";
import { cx } from "./cx";

/** The red dotted-edge postage stamp. Decorative. `wobble` makes it sway (waiting screens). */
export default function Stamp({
  wobble = false,
  large = false,
}: {
  wobble?: boolean;
  large?: boolean;
}) {
  return (
    <span
      className={cx(styles.stamp, wobble && styles.wobble, large && styles.large)}
      aria-hidden="true"
    >
      ✶
    </span>
  );
}
