import Link from "next/link";
import type { ComponentProps } from "react";

/** A quiet underlined internal link (secondary actions). */
export default function TextLink({ className, ...rest }: ComponentProps<typeof Link>) {
  return <Link className={className ? `text-link ${className}` : "text-link"} {...rest} />;
}
