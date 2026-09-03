"use client";

import { useId, useState } from "react";

const FLEET_SIZES = [
  "1–5 technicians",
  "6–15 technicians",
  "16–40 technicians",
  "More than 40 technicians",
];

type Status = "idle" | "sending" | "sent" | "error";

export default function DemoForm() {
  const id = useId();
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState("");

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form).entries());

    setStatus("sending");
    setMessage("");

    try {
      const res = await fetch("/api/demo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error ?? "Something went wrong.");
      }
      form.reset();
      setStatus("sent");
      setMessage(
        "Thanks — we have your details and will be in touch within one working day.",
      );
    } catch (err) {
      setStatus("error");
      setMessage(
        err instanceof Error
          ? `${err.message} You can also email hello@waypoint.example.`
          : "Something went wrong. You can also email hello@waypoint.example.",
      );
    }
  }

  const field =
    "mt-1.5 w-full rounded-md border border-line-strong bg-surface px-3 py-2.5 text-[14px] text-ink placeholder:text-muted/70 focus:border-accent";
  const label = "block text-[13px] font-medium text-ink-2";

  return (
    <form onSubmit={onSubmit} className="space-y-4" noValidate={false}>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor={`${id}-name`} className={label}>
            Name
          </label>
          <input
            id={`${id}-name`}
            name="name"
            type="text"
            required
            autoComplete="name"
            className={field}
            placeholder="Aisyah Rahman"
          />
        </div>

        <div>
          <label htmlFor={`${id}-company`} className={label}>
            Company
          </label>
          <input
            id={`${id}-company`}
            name="company"
            type="text"
            required
            autoComplete="organization"
            className={field}
            placeholder="Northwind Cooling"
          />
        </div>
      </div>

      <div>
        <label htmlFor={`${id}-email`} className={label}>
          Work email
        </label>
        <input
          id={`${id}-email`}
          name="email"
          type="email"
          required
          autoComplete="email"
          className={field}
          placeholder="you@company.com"
        />
      </div>

      <div>
        <label htmlFor={`${id}-fleet`} className={label}>
          Fleet size
        </label>
        <select
          id={`${id}-fleet`}
          name="fleetSize"
          required
          defaultValue=""
          className={`${field} appearance-none bg-[length:11px] bg-[right_0.9rem_center] bg-no-repeat pr-9 [background-image:url("data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20viewBox%3D%270%200%2010%206%27%3E%3Cpath%20d%3D%27M1%201l4%204%204-4%27%20fill%3D%27none%27%20stroke%3D%27%2362676f%27%20stroke-width%3D%271.4%27%2F%3E%3C%2Fsvg%3E")]`}
        >
          <option value="" disabled>
            Select a range
          </option>
          {FLEET_SIZES.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
      </div>

      <button
        type="submit"
        disabled={status === "sending"}
        className="w-full rounded-md bg-accent px-4 py-2.5 text-[14px] font-medium text-white transition-colors hover:bg-accent-dark disabled:cursor-not-allowed disabled:opacity-60"
      >
        {status === "sending" ? "Sending…" : "Book a demo"}
      </button>

      {/* Status is announced rather than only shown, and the region exists
          before submission so assistive tech picks the update up. */}
      <p
        role="status"
        aria-live="polite"
        className={`min-h-[1.25rem] text-[13px] leading-relaxed ${
          status === "error" ? "text-[#b91c1c]" : "text-muted"
        }`}
      >
        {message}
      </p>

      <p className="text-[12px] leading-relaxed text-muted">
        We use your details to arrange the demo and nothing else. No newsletter,
        no reselling.
      </p>
    </form>
  );
}
