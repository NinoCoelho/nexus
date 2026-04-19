import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./tokens.css";
import App from "./App";
import { ToastProvider } from "./toast/ToastProvider";

const root = document.getElementById("root");
if (!root) throw new Error("No #root element");
createRoot(root).render(
  <StrictMode>
    <ToastProvider>
      <App />
    </ToastProvider>
  </StrictMode>,
);
