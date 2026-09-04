"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ApiError, OfflineError, redeemCode } from "@/lib/api";
import { getToken, saveSession } from "@/lib/session";

// Sign in.
//
// No password, by design. A technician types the code dispatch read to them,
// and the API swaps it for a token the phone keeps. There is nothing to
// remember and nothing to reset.
//
// This is the one screen that genuinely needs signal. Worth saying out loud
// on the screen rather than showing a failure that looks like a wrong code --
// the difference between "you typed it wrong" and "you are in a basement" is
// the difference between trying again and walking outside.

// Set only on a public demo build, where there is no dispatcher to ask for a
// code. Empty everywhere else, and the sign-in screen is then exactly what a
// technician has always seen.
const DEMO_CODE = process.env.NEXT_PUBLIC_DEMO_CODE ?? "";

/** Insert the dash as they type: 8 characters shown as XXXX-XXXX. */
function format(raw: string): string {
  const clean = raw.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 8);
  return clean.length > 4 ? `${clean.slice(0, 4)}-${clean.slice(4)}` : clean;
}

function digits(value: string): number {
  return value.replace(/[^A-Z0-9]/g, "").length;
}

export default function LoginPage() {
  const router = useRouter();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Already signed in -- someone navigated here by hand, or the browser
  // restored the tab. Send them to their day rather than asking again.
  useEffect(() => {
    if (getToken()) router.replace("/");
  }, [router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (digits(code) !== 8 || busy) return;

    setBusy(true);
    setError(null);
    try {
      const session = await redeemCode(code);
      saveSession(session.token, {
        id: session.technician.id,
        name: session.technician.name,
        shift_start: session.technician.shift_start,
        shift_end: session.technician.shift_end,
      });
      router.replace("/");
    } catch (err) {
      if (err instanceof OfflineError) {
        setError("No connection. Signing in needs signal — try outside.");
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Something went wrong. Try again.");
      }
      setBusy(false);
      inputRef.current?.select();
    }
  }

  const ready = digits(code) === 8;

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col justify-center px-5 pb-16">
      <h1 className="text-[2rem] leading-tight font-bold">Waypoint</h1>
      <p className="mt-2 text-[1.05rem] text-ink-soft">
        Enter the code from dispatch.
      </p>

      <form onSubmit={submit} className="mt-7">
        <label htmlFor="code" className="sr-only">
          Access code
        </label>
        <input
          id="code"
          ref={inputRef}
          value={code}
          onChange={(e) => setCode(format(e.target.value))}
          disabled={busy}
          // autoComplete="one-time-code" gets iOS and Android to offer the
          // code straight from the SMS or clipboard instead of suggesting an
          // email address, which is what the default does on a short field.
          autoComplete="one-time-code"
          autoCapitalize="characters"
          autoCorrect="off"
          spellCheck={false}
          inputMode="text"
          enterKeyHint="go"
          autoFocus
          placeholder="XXXX-XXXX"
          aria-invalid={error !== null}
          aria-describedby={error ? "code-error" : undefined}
          className="w-full rounded-xl border-2 border-line bg-paper px-4 py-5 text-center font-mono text-[1.9rem] font-bold tracking-[0.14em] tabular-nums placeholder:text-ink-soft/45 focus:border-now focus:outline-none disabled:opacity-70"
        />

        {error ? (
          <p
            id="code-error"
            role="alert"
            className="mt-3 rounded-lg bg-alert px-4 py-3 text-[1rem] font-semibold text-white"
          >
            {error}
          </p>
        ) : null}

        <button
          data-tappable
          type="submit"
          disabled={!ready || busy}
          // min-h-[4.5rem] ~ 76px. This is the only control on the screen and
          // it gets tapped one-handed, so it is sized for a thumb rather than
          // for the text inside it.
          className="mt-5 min-h-[4.5rem] w-full rounded-xl bg-now text-[1.2rem] font-bold text-now-ink disabled:opacity-40"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>

      {DEMO_CODE ? (
        // Only rendered when this build was given a demo code. A real
        // deployment sets nothing and this whole block does not exist, so the
        // screen a technician sees is unchanged.
        <div className="mt-6 rounded-xl border border-dashed border-line p-4">
          <p className="font-mono text-[0.7rem] font-semibold uppercase tracking-[0.14em] text-ink-soft">
            Demo
          </p>
          <p className="mt-2 text-[0.95rem] text-ink-soft">
            There is no dispatcher to ask on a public demo, so here is a code
            that always works.
          </p>
          <button
            data-tappable
            type="button"
            onClick={() => {
              setCode(format(DEMO_CODE));
              setError(null);
              inputRef.current?.focus();
            }}
            className="mt-3 min-h-[3rem] w-full rounded-lg border-2 border-now bg-paper font-mono text-[1.15rem] font-bold tracking-[0.12em] tabular-nums text-ink"
          >
            {format(DEMO_CODE)}
          </button>
          <p className="mt-2 text-[0.85rem] text-ink-soft">
            Tap to fill it in, then Sign in.
          </p>
        </div>
      ) : (
        <p className="mt-6 text-[0.95rem] text-ink-soft">
          No code? Ask dispatch to issue one.
        </p>
      )}
    </main>
  );
}
