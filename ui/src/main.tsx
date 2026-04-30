/**
 * Nexus UI entry point.
 *
 * Mounts <App /> inside <StrictMode> with two context providers:
 *   - ThemeProvider — CSS theme switching (obsidian/ember/dawn)
 *   - ToastProvider — global toast notifications
 *
 * CSS loading order: tokens.css (design tokens) → App.css → component CSS.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./tokens.css";
import "./i18n"; // initialize i18next before any component reads from it
import App from "./App";
// mobile.css must load after App's component CSS so its @media overrides
// win the cascade (component CSS is imported transitively from <App />).
import "./mobile.css";
import { ThemeProvider } from "./theme/ThemeContext";
import { ToastProvider } from "./toast/ToastProvider";
import { SplashScreen } from "./components/SplashScreen";
import AuthGate from "./components/AuthGate";

const root = document.getElementById("root");
if (!root) throw new Error("No #root element");
createRoot(root).render(
  <StrictMode>
    <ThemeProvider>
      <ToastProvider>
        <AuthGate>
          <App />
          <SplashScreen />
        </AuthGate>
      </ToastProvider>
    </ThemeProvider>
  </StrictMode>,
);
