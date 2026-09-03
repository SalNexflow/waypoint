"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AccessStatus, IssuedCode, LockedError, api } from "@/lib/api";
import Unlock from "@/components/Unlock";

// Technician access codes.
//
// The only dispatcher-side screen the field app adds. It does one thing:
// gets a technician onto their phone, and gets them off it again.
//
// Deliberately separate from the console rather than a panel inside it. This
// is administration, done once per technician per handset; the solve UI is
// operational and used all day. Putting them on one screen would mean the
// dispatcher scrolls past "revoke access" every time they want to re-solve.

function expiry(iso: string): string {
  const d = new Date(iso);
  const hours = Math.round((d.getTime() - Date.now()) / 3_600_000);
  if (hours <= 0) return "expired";
  return `${hours}h`;
}

export default function AccessPage() {
  const [rows, setRows] = useState<AccessStatus[] | null>(null);
  const [issued, setIssued] = useState<IssuedCode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [locked, setLocked] = useState<LockedError | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api.accessStatus());
      setError(null);
    } catch (e) {
      // Locked is not an error to show in a banner -- there is nothing on
      // this screen to look at until it is resolved.
      if (e instanceof LockedError) setLocked(e);
      else setError(String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function issue(id: number) {
    setBusy(id);
    setError(null);
    try {
      setIssued(await api.issueAccessCode(id));
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function revoke(row: AccessStatus) {
    // Revoke logs a working phone out mid-shift, which is not recoverable
    // from this screen -- the technician needs a new code read to them. Worth
    // a confirm even though nothing else in this console has one.
    const what =
      row.active_devices > 0
        ? `Log ${row.technician_name} out of ${row.active_devices} device(s) and cancel any unused code?`
        : `Cancel ${row.technician_name}'s unused code?`;
    if (!window.confirm(what)) return;

    setBusy(row.technician_id);
    setError(null);
    try {
      await api.revokeAccess(row.technician_id);
      if (issued?.technician_id === row.technician_id) setIssued(null);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  if (locked) return <Unlock reason={locked.message} status={locked.status} />;

  return (
    <main className="shell">
      <header className="topbar">
        <h1>Technician access</h1>
        <Link className="navlink" href="/">
          ← Dispatch
        </Link>
      </header>

      {error && <p className="banner error">{error}</p>}

      {issued && (
        <div className="codepanel">
          <div>
            <p className="codepanel-who">
              Read this to <strong>{issued.technician_name}</strong>. It works
              once, and expires in {expiry(issued.expires_at)}.
            </p>
            {/* Shown once and never retrievable: the API stores a hash, not
                the code. Reissuing is the recovery path, not looking it up. */}
            <p className="codepanel-code">{issued.code}</p>
          </div>
          <button className="ghost" onClick={() => setIssued(null)}>
            Done
          </button>
        </div>
      )}

      <section className="access">
        {rows === null ? (
          <p className="empty">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="empty">No technicians. Seed a day first.</p>
        ) : (
          <table className="accesstable">
            <thead>
              <tr>
                <th>Technician</th>
                <th>Unused code</th>
                <th>Signed-in devices</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.technician_id}>
                  <td>{r.technician_name}</td>
                  <td>
                    {r.has_live_code && r.code_expires_at ? (
                      <span className="warn">
                        waiting · {expiry(r.code_expires_at)} left
                      </span>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td>
                    {r.active_devices > 0 ? (
                      <span className="ok">{r.active_devices}</span>
                    ) : (
                      <span className="muted">none</span>
                    )}
                  </td>
                  <td className="actions">
                    <button
                      onClick={() => issue(r.technician_id)}
                      disabled={busy !== null}
                    >
                      {r.has_live_code || r.active_devices > 0
                        ? "New code"
                        : "Issue code"}
                    </button>
                    <button
                      className="ghost"
                      onClick={() => revoke(r)}
                      disabled={
                        busy !== null ||
                        (!r.has_live_code && r.active_devices === 0)
                      }
                    >
                      Revoke
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </main>
  );
}
