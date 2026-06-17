export default {
  darkMode: ["class", ".dark"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"DM Sans"', "system-ui", "sans-serif"],
        serif: ['"Space Grotesk"', "system-ui", "sans-serif"],
        display: ['"Space Grotesk"', "system-ui", "sans-serif"],
        mono: ['"DM Mono"', '"Courier New"', "monospace"],
      },
      colors: {
        tier: {
          0: "#ef4444", // T0 raw/biometric — red
          1: "#38bdf8", // T1 events — sky
          2: "#f59e0b", // T2 aggregates — amber
          3: "#64748b", // T3 ops — slate
        },
        // Warm-residential accent — shades 300-600 are CSS-var-driven so palette
        // themes swap the accent without touching surfaces or tier colours.
        // Alpha modifiers (primary-500/40 etc.) work via the <alpha-value> placeholder.
        primary: {
          50:  "#fdf5ee",
          100: "#fae6d4",
          200: "#f4c9a3",
          300: "rgb(var(--primary-300) / <alpha-value>)",
          400: "rgb(var(--primary-400) / <alpha-value>)",
          500: "rgb(var(--primary-500) / <alpha-value>)",
          600: "rgb(var(--primary-600) / <alpha-value>)",
          700: "#9a4d0f",
          800: "#7c3f12",
          900: "#653513",
          950: "#3a1d0a",
        },
        // FROZEN — operator/admin surfaces only. Single-accent discipline: do NOT
        // use cool-* in resident-facing UI (Home/Rooms/Scenes/Cameras/Assistant).
        // The one accent is `primary` (palette-themeable); `warm` is a fixed
        // residential tint. Keep this scale static (not CSS-var-driven).
        cool: {
          300: "#a5b4fc",
          400: "#818cf8",
          500: "#6366f1",
          600: "#4f46e5",
        },
        warm: {
          50:  "#fff7ed",
          100: "#ffedd5",
          200: "#fed7aa",
          300: "#fdba74",
          400: "#fb923c",
          500: "#f97316",
          600: "#ea580c",
        },
      },
      animation: {
        "pulse-slow": "pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "fade-in":    "fadeIn 0.25s ease-out",
        "slide-up":   "slideUp 0.3s cubic-bezier(0.16, 1, 0.3, 1)",
        "shimmer":    "shimmer 2s linear infinite",
      },
      keyframes: {
        fadeIn: {
          "0%":   { opacity: "0" },
          "100%": { opacity: "1" },
        },
        slideUp: {
          "0%":   { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "0%":   { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
      },
      boxShadow: {
        "glass":        "0 8px 32px rgba(2,6,23,0.45), inset 0 1px 0 rgba(255,255,255,0.05)",
        "glass-light":  "0 4px 24px rgba(15,23,42,0.06), inset 0 1px 0 rgba(255,255,255,0.9)",
        "card":         "0 1px 2px rgba(2,6,23,0.30), 0 4px 16px rgba(2,6,23,0.20)",
        // Palette-themeable: track --primary-glow so the accent shadow follows the
        // selected theme (was hard-coded amber rgba(217,119,6,…) — desynced after multi-theme).
        "glow-primary": "0 0 0 1px var(--primary-glow), 0 6px 20px var(--primary-glow)",
        "gold":         "0 2px 12px var(--primary-glow)",
      },
    },
  },
  plugins: [],
};
