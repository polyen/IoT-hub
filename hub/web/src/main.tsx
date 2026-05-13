import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { Toaster } from "sonner";
import { QueryProvider } from "./app/providers/QueryProvider";
import { I18nProvider } from "./app/providers/I18nProvider";
import { ThemeProvider } from "./app/providers/ThemeProvider";
import { AuthProvider } from "./app/providers/AuthProvider";
import { AppRouter } from "./app/router";
import "./index.css";
import "./styles/tokens.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <ThemeProvider>
        <I18nProvider>
          <QueryProvider>
            <AuthProvider>
              <AppRouter />
              <Toaster position="top-right" richColors />
            </AuthProvider>
          </QueryProvider>
        </I18nProvider>
      </ThemeProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
