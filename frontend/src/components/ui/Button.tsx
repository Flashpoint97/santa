import type { ButtonHTMLAttributes } from "react";
import styles from "./Button.module.css";
import { cx } from "./cx";

/** The primary navy button. Full width on narrow screens. */
export default function Button({
  className,
  type = "button",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type={type} className={cx(styles.button, className)} {...rest} />;
}
