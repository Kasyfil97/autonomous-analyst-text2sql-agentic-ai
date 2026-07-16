// Standalone shim for next/link — renders a plain anchor. Navigation is inert
// in design-sync previews; the visual output (styles, layout, content) is
// identical to the real Link.
import * as React from "react";

type Href = string | { pathname?: string };
type LinkProps = { href: Href; children?: React.ReactNode } & Record<string, unknown>;

export default function Link({ href, children, ...rest }: LinkProps) {
  const h = typeof href === "string" ? href : href?.pathname ?? "#";
  return React.createElement("a", { href: h, ...rest }, children);
}
