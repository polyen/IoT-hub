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
        primary: {
          50:  "#eef2ff",
          100: "#e0e7ff",
          200: "#c7d2fe",
          300: "#a5b4fc",
          400: "#818cf8",
          500: "#6366f1",
          600: "#4f46e5",
          700: "#4338ca",
          800: "#3730a3",
          900: "#312e81",
          950: "#1e1b4b",
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
        "glow-primary": "0 0 0 1px rgba(99,102,241,0.35), 0 6px 20px rgba(99,102,241,0.30)",
        "gold":         "0 2px 12px rgba(99,102,241,0.25)",
      },
    },
  },
  plugins: [],
};
