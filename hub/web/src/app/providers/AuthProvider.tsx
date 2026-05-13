import { createContext, useContext, useState } from "react";

type UserRole = "vlad" | "guest" | "default";

interface AuthCtx {
  user: UserRole;
  setUser: (u: UserRole) => void;
}

const Ctx = createContext<AuthCtx>({ user: "vlad", setUser: () => {} });

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserRole>(
    () => (localStorage.getItem("user") as UserRole) ?? "vlad",
  );

  const handleSet = (u: UserRole) => {
    setUser(u);
    localStorage.setItem("user", u);
  };

  return <Ctx.Provider value={{ user, setUser: handleSet }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
