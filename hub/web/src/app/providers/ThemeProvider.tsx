import { createContext, useContext, useEffect, useState } from "react";

type Theme = "dark" | "light";

export type Palette = "terracotta" | "nature" | "sand" | "aubergine";

export const PALETTES: { id: Palette; label: string; swatch: string }[] = [
  { id: "terracotta", label: "Теракота", swatch: "#e07a3f" },
  { id: "nature",     label: "Природа",  swatch: "#d98b5f" },
  { id: "sand",       label: "Бірюза",   swatch: "#2fb6a8" },
  { id: "aubergine",  label: "Баклажан", swatch: "#d19502" },
];

const PALETTE_IDS = PALETTES.map((p) => p.id);

interface ThemeCtx {
  theme: Theme;
  toggle: () => void;
  palette: Palette;
  setPalette: (p: Palette) => void;
}

const Ctx = createContext<ThemeCtx>({
  theme: "dark",
  toggle: () => {},
  palette: "terracotta",
  setPalette: () => {},
});

function getInitialTheme(): Theme {
  const stored = localStorage.getItem("theme") as Theme | null;
  if (stored === "dark" || stored === "light") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function getInitialPalette(): Palette {
  const stored = localStorage.getItem("palette") as Palette | null;
  if (stored && (PALETTE_IDS as string[]).includes(stored)) return stored;
  return "terracotta";
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);
  const [palette, setPaletteState] = useState<Palette>(getInitialPalette);

  useEffect(() => {
    const root = document.documentElement;
    root.classList.remove("dark", "light");
    root.classList.add(theme);
    localStorage.setItem("theme", theme);
  }, [theme]);

  useEffect(() => {
    document.documentElement.setAttribute("data-palette", palette);
    localStorage.setItem("palette", palette);
  }, [palette]);

  const toggle = () => setTheme((t) => (t === "dark" ? "light" : "dark"));

  const setPalette = (p: Palette) => setPaletteState(p);

  return (
    <Ctx.Provider value={{ theme, toggle, palette, setPalette }}>
      {children}
    </Ctx.Provider>
  );
}

export const useTheme = () => useContext(Ctx);
