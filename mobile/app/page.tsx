import { TodayScreen } from "@/components/TodayScreen";

/**
 * Today -- screen 1 of 4.
 *
 * A thin server component over a client one. The session lives in the
 * browser, so the whole screen is gated client-side; keeping this file a
 * server component means the route still prerenders to static HTML and the
 * shell is cacheable by the service worker.
 *
 * The jobs come from `GET /field/today`, scoped to the technician the bearer
 * token resolves to. Phase 6 makes the result survive a dead zone.
 */
export default function TodayPage() {
  return <TodayScreen />;
}
