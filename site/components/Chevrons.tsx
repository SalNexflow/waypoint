/**
 * The angular motif: a band of chevrons, the same shape as the arrow in the
 * mark and the "→" that opens each step. Flat geometry at low opacity, sitting
 * inside the wash — no blobs, nothing floating.
 */
export default function Chevrons({ className = "" }: { className?: string }) {
  const chevron = "M0 0h150l160 240-160 240H0l160-240z";
  return (
    <svg
      viewBox="0 0 900 480"
      preserveAspectRatio="xMaxYMid slice"
      aria-hidden="true"
      focusable="false"
      className={className}
    >
      <g fill="#ffffff" opacity="0.5">
        <path d={chevron} transform="translate(60 0)" />
        <path d={chevron} transform="translate(320 0)" />
      </g>
      <g fill="#ffffff" opacity="0.32">
        <path d={chevron} transform="translate(580 0)" />
      </g>
    </svg>
  );
}
