import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      manifest: {
        name: "IoT Hub",
        short_name: "IoTHub",
        description: "Local-first smart home control panel",
        theme_color: "#4f46e5",
        background_color: "#0b1120",
        display: "standalone",
        icons: [
          { src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" },
          { src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "maskable" },
        ],
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,ico,png,svg,woff2}"],
        navigateFallback: "index.html",
        // Activate a new SW immediately on deploy so a stale worker never keeps
        // serving an old app on one origin (the iot-hub.local vs IP mismatch).
        clientsClaim: true,
        skipWaiting: true,
        runtimeCaching: [
          {
            // API and media: bypass SW entirely — real-time data must not be cached.
            // Match on pathname (a `^/…` RegExp does NOT match the full request URL).
            urlPattern: ({ url }) => /^\/(api|hls|whep|ws)\//.test(url.pathname),
            handler: "NetworkOnly",
          },
          {
            // Static assets: cache-first
            urlPattern: /\.(js|css|png|svg|ico|woff2)$/,
            handler: "CacheFirst",
            options: {
              cacheName: "static-cache",
              expiration: { maxEntries: 100, maxAgeSeconds: 604800 },
            },
          },
        ],
      },
    }),
  ],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
  build: {
    chunkSizeWarningLimit: 600,
  },
});
