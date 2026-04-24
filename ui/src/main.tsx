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
import App from "./App";
import { ThemeProvider } from "./theme/ThemeContext";
import { ToastProvider } from "./toast/ToastProvider";

const root = document.getElementById("root");
if (!root) throw new Error("No #root element");
createRoot(root).render(
  <StrictMode>
    <ThemeProvider>
      <ToastProvider>
        <App />
      </ToastProvider>
    </ThemeProvider>
  </StrictMode>,
);
