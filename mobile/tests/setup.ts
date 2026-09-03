// Browser globals the queue needs, and nothing else.

// A real IndexedDB implementation, in memory. The queue is the one part of
// this app that MUST survive an app close and a device restart, so testing it
// against a stub of our own design would be testing the stub. fake-indexeddb
// runs the actual W3C algorithms -- transaction lifetimes, auto-commit,
// key ordering, the lot -- which is where the interesting bugs live.
import "fake-indexeddb/auto";

// localStorage, which the session and the cached day use. Fifteen lines
// rather than a DOM environment dependency: this is the whole API surface
// those modules touch.
class MemoryStorage implements Storage {
  private map = new Map<string, string>();
  get length() {
    return this.map.size;
  }
  clear() {
    this.map.clear();
  }
  getItem(k: string) {
    return this.map.has(k) ? this.map.get(k)! : null;
  }
  key(i: number) {
    return [...this.map.keys()][i] ?? null;
  }
  removeItem(k: string) {
    this.map.delete(k);
  }
  setItem(k: string, v: string) {
    this.map.set(k, String(v));
  }
}

const storage = new MemoryStorage();
// The app reads `window.localStorage`, guarded by a `typeof window` check, so
// both have to exist for those guards to take the browser branch.
Object.defineProperty(globalThis, "localStorage", { value: storage });
Object.defineProperty(globalThis, "window", {
  value: { localStorage: storage, addEventListener() {}, removeEventListener() {} },
  writable: true,
});
