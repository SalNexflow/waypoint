import type { Metadata } from "next";
import { Inter, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono-plex",
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL("https://waypoint.example"),
  title: "Waypoint — field service dispatch that solves the whole day",
  description:
    "Waypoint assigns and routes a day's jobs across your technicians against real road travel times, respecting skills, time windows, shifts and van stock — and re-solves in seconds when the day changes.",
  openGraph: {
    title: "Waypoint — field service dispatch that solves the whole day",
    description:
      "Constraint-based scheduling for field service teams. Real road travel times, skill and parts matching, mid-day re-optimisation.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${inter.variable} ${plexMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
