// IndexedDB, and the serial queue that every write goes through.
//
// Two jobs, kept in one file because they are the same concern: this is the
// only place in the app that touches durable storage, and the only place that
// decides what "one at a time" means.

const DB_NAME = "waypoint-field";
const DB_VERSION = 1;

/** The outbox: things done on the phone that the server has not accepted yet. */
export const OUTBOX = "outbox";

/**
 * Promise wrapper for an IDBRequest.
 *
 * IndexedDB predates promises and is entirely event-driven, so every single
 * operation is a request object with `onsuccess` / `onerror`. Wrapping it once
 * is what lets the rest of this file read like ordinary async code.
 *
 * The catch, and it is a real one: an IndexedDB transaction stays open only
 * while requests are pending on it, and auto-commits as soon as the microtask
 * queue drains without one. So `await`ing anything that is NOT an IDB request
 * inside a transaction silently closes it, and the next operation throws
 * TransactionInactiveError. Every function below opens its own transaction and
 * awaits nothing else inside it.
 */
function promisify<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

let dbPromise: Promise<IDBDatabase> | null = null;

export function openDb(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise;

  dbPromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(OUTBOX)) {
        // `keyPath: "seq"` with autoIncrement puts the key ON the record
        // rather than leaving it out-of-line, so a value read back knows its
        // own position without the caller carrying it separately.
        //
        // That key is doing double duty. It is the queue order -- IndexedDB
        // guarantees a monotonically increasing auto-increment within a store
        // -- and it is the `device_seq` sent to the server. Deriving both
        // from one transactional counter is stricter than the localStorage
        // counter it replaces: that one could be incremented and then lose
        // the write it was counting.
        const store = db.createObjectStore(OUTBOX, {
          keyPath: "seq",
          autoIncrement: true,
        });
        // Draining walks pending items in key order; failed ones are skipped
        // rather than deleted, so they need to be findable separately.
        store.createIndex("state", "state", { unique: false });
      }
    };

    request.onsuccess = () => {
      const db = request.result;
      // Another tab (or an update) wants a new version and cannot get it
      // while this connection is open. Close and forget, so the next call
      // reopens cleanly rather than deadlocking the upgrade.
      db.onversionchange = () => {
        db.close();
        dbPromise = null;
      };
      resolve(db);
    };

    request.onerror = () => reject(request.error);
    request.onblocked = () =>
      reject(new Error("indexeddb upgrade blocked by another tab"));
  });

  // A failed open must not be cached, or the app is permanently broken until
  // it is force-quit.
  dbPromise.catch(() => {
    dbPromise = null;
  });

  return dbPromise;
}

/** Test seam: drop the cached connection so a fresh database can be opened. */
export function resetDbForTests(): void {
  dbPromise = null;
}

// --- The serial queue -------------------------------------------------------

let tail: Promise<unknown> = Promise.resolve();

/**
 * Run `op` after every operation handed to `serial` before it, and before
 * every one handed to it after. One at a time, in call order, always.
 *
 * WHY THIS EXISTS
 * ---------------
 * IndexedDB transactions are individually atomic, which is not the same thing
 * as safe. The races here are at the application level and span several
 * transactions: a drain reading the queue, deciding an item succeeded, and
 * deleting it, while an enqueue appends -- or two drains, triggered by an
 * `online` event and a visibility change landing together, both reading the
 * same pending item and both sending it. The second one is the failure the
 * spec's "no duplicates, verified by event count" is about, and no amount of
 * per-transaction atomicity prevents it.
 *
 * A single promise chain does. Everything that touches the outbox goes
 * through here, so "concurrent" stops being a state the queue can be in.
 *
 * TWO DETAILS THAT MATTER
 * -----------------------
 * `tail.then(op, op)` passes `op` as BOTH handlers, so a rejected predecessor
 * still lets the next operation run. Using `.then(op)` alone would mean one
 * failed write -- a full disk, a closed connection -- wedged the queue
 * permanently.
 *
 * `tail = run.catch(...)` stores the SWALLOWED promise as the new tail, not
 * `run` itself. Storing `run` would leave a rejected promise nobody handles,
 * which surfaces as an unhandled rejection and, in a service worker context,
 * can take the whole registration down. The caller still receives `run` and
 * still sees the rejection.
 */
export function serial<T>(op: () => Promise<T>): Promise<T> {
  const run = tail.then(op, op);
  tail = run.catch(() => undefined);
  return run;
}

/** Wait for everything currently queued to settle. Tests and sign-out use it. */
export function drained(): Promise<unknown> {
  return tail;
}

// --- Store access -----------------------------------------------------------

/**
 * Run `body` inside one transaction on the outbox.
 *
 * `body` is synchronous by design: it issues IDB requests and returns their
 * promises, and the transaction stays alive because those requests keep it
 * alive. Anything that needs to await a fetch does it OUTSIDE, between
 * transactions -- which is exactly why draining reads a batch, closes the
 * transaction, sends over the network, and only then reopens to delete.
 */
export async function withOutbox<T>(
  mode: IDBTransactionMode,
  body: (store: IDBObjectStore) => Promise<T>,
): Promise<T> {
  const db = await openDb();
  const tx = db.transaction(OUTBOX, mode);
  const result = body(tx.objectStore(OUTBOX));

  // Wait for the transaction itself, not just the requests. A request can
  // succeed and the transaction still abort afterwards (quota, for one), and
  // treating that as a success would delete a queued item that was never
  // durably written.
  const committed = new Promise<void>((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error ?? new Error("transaction aborted"));
  });

  const value = await result;
  if (mode === "readwrite") await committed;
  return value;
}

export { promisify };
