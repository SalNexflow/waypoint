import type { ReactNode } from "react";

/** Shared layout primitives. One container width, one section rhythm. */

export function Container({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`mx-auto w-full max-w-6xl px-5 sm:px-8 ${className}`}>
      {children}
    </div>
  );
}

/**
 * A numbered section label. The index is decorative — screen readers get the
 * heading itself, which each section wires up with aria-labelledby.
 */
export function SectionLabel({
  index,
  children,
}: {
  index: string;
  children: ReactNode;
}) {
  return (
    <p className="flex items-center gap-3 font-mono text-[11px] uppercase tracking-[0.14em] text-muted">
      <span aria-hidden="true">{index}</span>
      <span aria-hidden="true" className="h-px w-6 bg-line-strong" />
      <span>{children}</span>
    </p>
  );
}

export function SectionHeading({
  id,
  children,
  className = "",
}: {
  id: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <h2
      id={id}
      className={`mt-5 max-w-3xl text-pretty text-2xl font-medium leading-[1.15] tracking-[-0.02em] sm:text-[32px] ${className}`}
    >
      {children}
    </h2>
  );
}

export function PrimaryLink({
  href,
  children,
  className = "",
}: {
  href: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <a
      href={href}
      className={`inline-flex items-center justify-center rounded-md bg-accent px-4 py-2.5 text-[14px] font-medium text-white transition-colors hover:bg-accent-dark ${className}`}
    >
      {children}
    </a>
  );
}

export function SecondaryLink({
  href,
  children,
}: {
  href: string;
  children: ReactNode;
}) {
  return (
    <a
      href={href}
      className="inline-flex items-center justify-center gap-1.5 rounded-md border border-line-strong bg-surface px-4 py-2.5 text-[14px] font-medium text-ink transition-colors hover:border-ink-2"
    >
      {children}
    </a>
  );
}
