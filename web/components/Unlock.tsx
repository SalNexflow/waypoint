"use client";

import { useState } from "react";
import { setDispatchToken } from "@/lib/api";

/**
 * The console's front door.
 *
 * One field, because there is one secret. This is not a login -- there are no
 * dispatcher accounts and this does not pretend otherwise. It answers "is
 * this the office", which is the question that needed answering once the API
 * stopped being reachable only from the machine it runs on.
 *
 * Shown for a 503 as well as a 401, because "the token is wrong" and "the API
 * is exposed with no token configured" are the same thing from here: you
 * cannot use the console until somebody sets something.
 */
export default function Unlock({
  reason,
  status,
}: {
  reason: string;
  status: number;
}) {
  const [value, setValue] = useState("");

  return (
    <main className="shell">
      <header className="topbar">
        <h1>Waypoint</h1>
      </header>
      <section className="unlock">
        <h2>Dispatcher token</h2>
        {status === 503 ? (
          <>
            <p className="unlock-why">
              This API is reachable from outside this machine and no
              <code> DISPATCH_TOKEN </code> is set, so it is refusing to serve
              the console at all.
            </p>
            <p className="unlock-why">
              Set one and restart the api service:
            </p>
            <pre className="unlock-cmd">
{`python -c "import secrets; print(secrets.token_urlsafe(24))"
# put it in .env as DISPATCH_TOKEN=...
docker compose up -d api`}
            </pre>
          </>
        ) : (
          <p className="unlock-why">{reason}</p>
        )}

        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!value.trim()) return;
            setDispatchToken(value.trim());
            // A full reload rather than re-running the fetches by hand: every
            // panel on this page loads independently, and the simplest way to
            // get all of them to try again with the new token is to start over.
            window.location.reload();
          }}
        >
          <input
            type="password"
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="DISPATCH_TOKEN"
            aria-label="Dispatcher token"
          />
          <button type="submit" disabled={!value.trim()}>
            Unlock
          </button>
        </form>
      </section>
    </main>
  );
}
