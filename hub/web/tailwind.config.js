export default {
  darkMode: ["class", ".dark"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        tier: {
          0: "#ef4444",
          1: "#f59e0b",
          2: "#3b82f6",
          3: "#64748b",
        },
      },
    },
  },
  plugins: [],
};
