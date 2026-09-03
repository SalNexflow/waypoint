"use client";

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import { Route, Unassigned, colourFor } from "@/lib/api";

interface Props {
  routes: Route[];
  unassigned: Unassigned[];
  selectedTech: string | null;
  onSelectJob?: (jobId: number, techRef: string) => void;
}

// A raster basemap from OpenStreetMap tiles. No API key, no vector style
// server, and the routes are what matter here rather than cartography.
const STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "&copy; OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

export default function DayMap({
  routes,
  unassigned,
  selectedTech,
  onSelectJob,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const markers = useRef<maplibregl.Marker[]>([]);

  useEffect(() => {
    if (!container.current || map.current) return;
    map.current = new maplibregl.Map({
      container: container.current,
      style: STYLE,
      center: [101.6869, 3.1339], // KL Sentral
      zoom: 10.5,
      attributionControl: { compact: true },
    });
    map.current.addControl(new maplibregl.NavigationControl(), "top-right");
    return () => {
      map.current?.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;

    const draw = () => {
      // Clear previous render.
      markers.current.forEach((mk) => mk.remove());
      markers.current = [];
      for (const layer of m.getStyle().layers ?? []) {
        if (layer.id.startsWith("route-")) m.removeLayer(layer.id);
      }
      for (const id of Object.keys(m.getStyle().sources ?? {})) {
        if (id.startsWith("route-")) m.removeSource(id);
      }

      const bounds = new maplibregl.LngLatBounds();
      let any = false;

      routes.forEach((route, i) => {
        if (route.visits.length === 0) return;
        const dim = selectedTech !== null && selectedTech !== route.technician_ref;
        const colour = colourFor(i);

        // The line runs home -> job -> job ... These are straight segments
        // between stops, not the driven road geometry. The road shape would
        // need a route call per leg; the ordering is what a dispatcher reads
        // off the map, and the travel *times* come from OSRM regardless.
        const coords: [number, number][] = [
          [route.home_lon, route.home_lat],
          ...route.visits.map((v) => [v.lon, v.lat] as [number, number]),
        ];
        coords.forEach((c) => {
          bounds.extend(c);
          any = true;
        });

        const id = `route-${route.technician_ref}`;
        m.addSource(id, {
          type: "geojson",
          data: {
            type: "Feature",
            properties: {},
            geometry: { type: "LineString", coordinates: coords },
          },
        });
        m.addLayer({
          id: `${id}-line`,
          type: "line",
          source: id,
          paint: {
            "line-color": colour,
            "line-width": dim ? 1.5 : 3,
            "line-opacity": dim ? 0.25 : 0.85,
          },
        });

        // Home marker: a square, so it is distinguishable from job pins.
        const home = document.createElement("div");
        home.style.cssText = `width:14px;height:14px;background:${colour};border:2px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.3);opacity:${dim ? 0.3 : 1}`;
        home.title = `${route.technician_name} — home`;
        markers.current.push(
          new maplibregl.Marker({ element: home })
            .setLngLat([route.home_lon, route.home_lat])
            .addTo(m),
        );

        route.visits.forEach((v) => {
          const el = document.createElement("div");
          el.style.cssText = `
            width:24px;height:24px;border-radius:50%;
            background:${colour};color:#fff;border:2px solid #fff;
            display:flex;align-items:center;justify-content:center;
            font:600 11px/1 system-ui,sans-serif;cursor:pointer;
            box-shadow:0 1px 4px rgba(0,0,0,.35);opacity:${dim ? 0.3 : 1}`;
          el.textContent = String(v.sequence + 1);
          el.title = `${v.job_ref} ${v.customer}\n${route.technician_name}\n${v.start}–${v.end}`;
          el.onclick = () => onSelectJob?.(v.job_id, route.technician_ref);

          markers.current.push(
            new maplibregl.Marker({ element: el })
              .setLngLat([v.lon, v.lat])
              .setPopup(
                new maplibregl.Popup({ offset: 16 }).setHTML(
                  `<strong>${v.job_ref} ${v.customer}</strong><br/>
                   ${route.technician_name}<br/>
                   ${v.start}–${v.end}${v.wait_minutes > 0 ? ` (waited ${v.wait_minutes}m)` : ""}`,
                ),
              )
              .addTo(m),
          );
        });
      });

      // Unassigned jobs: hollow grey rings, deliberately visible. A job that
      // did not fit is the thing a dispatcher most needs to see.
      unassigned.forEach((u) => {
        const el = document.createElement("div");
        el.style.cssText = `
          width:20px;height:20px;border-radius:50%;
          background:#fff;border:3px dashed #71717a;cursor:pointer;
          box-shadow:0 1px 3px rgba(0,0,0,.3)`;
        el.title = `${u.job_ref} ${u.customer} — UNASSIGNED\n${u.message}`;
        markers.current.push(
          new maplibregl.Marker({ element: el })
            .setLngLat([u.lon, u.lat])
            .setPopup(
              new maplibregl.Popup({ offset: 14 }).setHTML(
                `<strong>${u.job_ref} ${u.customer}</strong><br/>
                 <em>unassigned — ${u.reason.replace(/_/g, " ")}</em><br/>
                 ${u.message}`,
              ),
            )
            .addTo(m),
        );
        bounds.extend([u.lon, u.lat]);
        any = true;
      });

      if (any && !bounds.isEmpty()) {
        m.fitBounds(bounds, { padding: 60, maxZoom: 13, duration: 400 });
      }
    };

    if (m.isStyleLoaded()) draw();
    else m.once("load", draw);
  }, [routes, unassigned, selectedTech, onSelectJob]);

  return (
    <div
      ref={container}
      style={{ width: "100%", height: "100%", background: "#e5e7eb" }}
    />
  );
}
