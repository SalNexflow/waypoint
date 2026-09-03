import type { Metadata, Viewport } from "next";
import "./globals.css";
import { RegisterServiceWorker } from "./register-sw";

export const metadata: Metadata = {
  title: "Waypoint Field",
  description: "Your day, in order.",
  manifest: "/manifest.webmanifest",
  // iOS ignores the manifest's icons entirely and reads this instead. Without
  // it, "Add to Home Screen" on an iPhone produces a screenshot of the page
  // as the icon.
  appleWebApp: {
    capable: true,
    title: "Waypoint",
    statusBarStyle: "default",
  },
  icons: {
    icon: [
      { url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: [{ url: "/icons/apple-touch-icon.png", sizes: "180x180" }],
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // PINCH-ZOOM IS ALLOWED, and the phase 1 reasoning for blocking it was
  // wrong.
  //
  // The justification was "the app must not scale on input focus" -- iOS
  // zooms into a focused input whose font-size is under 16px. Every input
  // here is well over that (the code field is 1.9rem, the notes field
  // 1.05rem on a 17px base), so the behaviour being prevented could not
  // happen. `user-scalable=no` was buying nothing.
  //
  // What it cost was real: a technician with low vision could not zoom, which
  // is a WCAG 1.4.4 failure and the only thing keeping the Lighthouse
  // accessibility score off 100. Large type is not a substitute for letting
  // somebody make it larger.
  // Extends the layout under the notch. Paired with the safe-area padding in
  // globals.css -- one without the other puts content behind the hardware.
  viewportFit: "cover",
  themeColor: "#1a49c4",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <RegisterServiceWorker />
        {children}
      </body>
    </html>
  );
}
