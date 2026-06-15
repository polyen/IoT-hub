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
          { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
        ],
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,ico,png,svg,woff2}"],
        navigateFallback: "index.html",
        runtimeCaching: [
          {
            // API and media: bypass SW entirely — real-time data must not be cached
            urlPattern: /^\/(api|hls|whep|ws)\/.*/,
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
