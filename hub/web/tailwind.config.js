export default {
  darkMode: ["class", ".dark"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"DM Sans"', "system-ui", "sans-serif"],
        serif: ['"Playfair Display"', "Georgia", "serif"],
        display: ['"Playfair Display"', "Georgia", "serif"],
        mono: ['"DM Mono"', '"Courier New"', "monospace"],
      },
      colors: {
        tier: {
          0: "#ef4444",
          1: "#d4a017",
          2: "#c9a84c",
          3: "#64748b",
        },
        primary: {
          50:  "#fdf8ec",
          100: "#f9efc8",
          200: "#f2dc90",
          300: "#e8c95a",
          400: "#d9b53d",
          500: "#c9a84c",
          600: "#a8872b",
          700: "#846518",
          800: "#694e12",
          900: "#4d390e",
          950: "#2c1f07",
        },
        warm: {
          50:  "#fdf4ef",
          100: "#fae3d2",
          200: "#f5c5a3",
          300: "#eda473",
          400: "#e58348",
          500: "#d4763b",
          600: "#b85e27",
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
        "glass":        "0 8px 32px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.04)",
        "glass-light":  "0 4px 24px rgba(0,0,0,0.06), inset 0 1px 0 rgba(255,255,255,0.9)",
        "glow-primary": "0 0 20px rgba(201,168,76,0.2), 0 0 40px rgba(201,168,76,0.08)",
        "gold":         "0 2px 12px rgba(201,168,76,0.25)",
      },
    },
  },
  plugins: [],
};
