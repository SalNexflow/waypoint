/**
 * A v4 UUID, on every device this app will actually run on.
 *
 * `crypto.randomUUID()` is the obvious answer and it is a trap here: it is
 * gated on a **secure context**, so it exists on `https://` and on
 * `localhost`, and is `undefined` on `http://192.168.1.42:3002` -- which is
 * exactly how the app gets loaded onto a real phone for testing. The failure
 * would be a TypeError the first time a technician tapped the action button,
 * on the one device configuration nobody develops against.
 *
 * `crypto.getRandomValues()` carries no such gate, so the fallback builds the
 * same thing by hand. It is not a downgrade in randomness -- both draw from
 * the same CSPRNG -- only in convenience.
 *
 * `Math.random()` is never used. These ids are the idempotency key for the
 * offline queue: a collision would mean one technician's status event
 * silently discarded as a duplicate of another's.
 */
export function newUuid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }

  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);

  // Version 4, variant 1 -- the two fixed fields that make this a well-formed
  // random UUID rather than 16 arbitrary bytes.
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;

  const hex: string[] = [];
  for (const b of bytes) hex.push(b.toString(16).padStart(2, "0"));
  const s = hex.join("");
  return `${s.slice(0, 8)}-${s.slice(8, 12)}-${s.slice(12, 16)}-${s.slice(16, 20)}-${s.slice(20)}`;
}
