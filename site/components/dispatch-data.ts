/**
 * Geometry for the dispatch-view rendering in the hero.
 *
 * Coordinates are Klang Valley job locations (the same districts the product's
 * seed generator uses) projected into a 1000x620 viewBox, then ordered
 * nearest-neighbour from each technician's start point so the drawn polylines
 * look like routes someone would drive. Generated once and frozen here — the
 * page ships static geometry rather than running a generator at render time.
 *
 * This is a rendering of the dispatch view, not a live screenshot. Replace it
 * with a real capture from web/ once there is a day worth photographing:
 * drop the PNG in public/ and swap <DispatchView /> for next/image in Hero.
 */

export interface Route {
  name: string;
  skill: string;
  /** Shift start location, viewBox units. */
  home: [number, number];
  /** Job locations in visit order, viewBox units. */
  stops: [number, number][];
  /** Timeline blocks: minutes from midnight, service duration in minutes. */
  jobs: { start: number; dur: number; label: string }[];
}

/** Copied from the product UI (web/lib/api.ts) so the colours match. */
export const ROUTE_COLOURS = [
  "#2563eb",
  "#dc2626",
  "#16a34a",
  "#ea580c",
  "#9333ea",
  "#0891b2",
  "#ca8a04",
  "#db2777",
] as const;

export const ROUTES: Route[] = [
  {
    name: "Aisyah R.",
    skill: "VRV/VRF",
    home: [660.5, 105.1],
    stops: [
      [660.9, 91.6], [680.8, 92.2], [706.3, 138.8], [738.3, 159.7], [785.3, 86.6], [821.1, 83.3],
    ],
    jobs: [
      { start: 499, dur: 45, label: "J-1200" },
      { start: 580, dur: 75, label: "J-1203" },
      { start: 679, dur: 60, label: "J-1206" },
      { start: 761, dur: 75, label: "J-1209" },
      { start: 863, dur: 60, label: "J-1212" },
      { start: 957, dur: 90, label: "J-1215" },
    ],
  },
  {
    name: "Danish O.",
    skill: "Chiller",
    home: [844.8, 280],
    stops: [
      [855.9, 263.7], [797.5, 267.9], [613.2, 300.7], [660.8, 378], [702, 389.7],
    ],
    jobs: [
      { start: 496, dur: 60, label: "J-1213" },
      { start: 577, dur: 90, label: "J-1216" },
      { start: 695, dur: 45, label: "J-1219" },
      { start: 777, dur: 75, label: "J-1222" },
      { start: 893, dur: 90, label: "J-1225" },
    ],
  },
  {
    name: "Farid M.",
    skill: "Ducted",
    home: [417.1, 240.9],
    stops: [
      [394.6, 240.6], [352.1, 231.2], [500.8, 156.4], [580.5, 150.4], [634, 194.4], [655.3, 197.5],
    ],
    jobs: [
      { start: 492, dur: 60, label: "J-1226" },
      { start: 575, dur: 45, label: "J-1229" },
      { start: 656, dur: 75, label: "J-1232" },
      { start: 757, dur: 60, label: "J-1235" },
      { start: 844, dur: 45, label: "J-1238" },
      { start: 913, dur: 90, label: "J-1241" },
    ],
  },
  {
    name: "Hafiz S.",
    skill: "Split unit",
    home: [134.7, 295.2],
    stops: [
      [141.9, 307.3], [106, 301.3], [96, 288], [299.3, 346.7], [356.3, 326.6],
    ],
    jobs: [
      { start: 500, dur: 45, label: "J-1239" },
      { start: 567, dur: 60, label: "J-1242" },
      { start: 653, dur: 60, label: "J-1245" },
      { start: 742, dur: 60, label: "J-1248" },
      { start: 838, dur: 60, label: "J-1251" },
    ],
  },
  {
    name: "Izzat K.",
    skill: "Electrical",
    home: [887.7, 157.7],
    stops: [
      [834.3, 143.5], [827, 179.7], [764.8, 163.1], [723.9, 155.9], [688, 148.4], [718.8, 130],
    ],
    jobs: [
      { start: 498, dur: 60, label: "J-1252" },
      { start: 589, dur: 75, label: "J-1255" },
      { start: 696, dur: 60, label: "J-1258" },
      { start: 780, dur: 60, label: "J-1261" },
      { start: 866, dur: 60, label: "J-1264" },
      { start: 961, dur: 60, label: "J-1267" },
    ],
  },
  {
    name: "Nadia L.",
    skill: "Refrigerant",
    home: [444.9, 367.6],
    stops: [
      [481.3, 373.2], [409, 337.1], [362.1, 334.4], [287.9, 328], [628.7, 260.1],
    ],
    jobs: [
      { start: 494, dur: 60, label: "J-1265" },
      { start: 575, dur: 60, label: "J-1268" },
      { start: 654, dur: 60, label: "J-1271" },
      { start: 737, dur: 60, label: "J-1274" },
      { start: 825, dur: 75, label: "J-1277" },
    ],
  },
  {
    name: "Rizal A.",
    skill: "Split unit",
    home: [492.4, 82.1],
    stops: [
      [497.3, 87.6], [507.2, 65.2], [585.3, 114], [569.1, 143.7], [658.6, 116.9],
    ],
    jobs: [
      { start: 496, dur: 60, label: "J-1278" },
      { start: 578, dur: 90, label: "J-1281" },
      { start: 700, dur: 90, label: "J-1284" },
      { start: 831, dur: 60, label: "J-1287" },
      { start: 913, dur: 75, label: "J-1290" },
    ],
  },
  {
    name: "Suria T.",
    skill: "VRV/VRF",
    home: [661.2, 317.4],
    stops: [
      [655.9, 333.7], [618.2, 324.7], [577.5, 252.2], [559, 246.7], [665, 184.3],
    ],
    jobs: [
      { start: 484, dur: 60, label: "J-1291" },
      { start: 576, dur: 90, label: "J-1294" },
      { start: 697, dur: 90, label: "J-1297" },
      { start: 827, dur: 75, label: "J-1300" },
      { start: 921, dur: 45, label: "J-1303" },
    ],
  },];

/** Two jobs the solver could not place — shown on the map as open markers. */
export const UNASSIGNED: { at: [number, number]; reason: string }[] = [
  { at: [186.4, 322.6], reason: "no chiller-qualified technician" },
  { at: [852.7, 358.1], reason: "time window unreachable" },
];

export const DAY_START = 7 * 60;
export const DAY_END = 19 * 60;
