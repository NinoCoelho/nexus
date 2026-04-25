import type { CapacitorConfig } from "@capacitor/cli";

// Backend host for the Capacitor build. Set CAP_NEXUS_API (or
// VITE_NEXUS_API) to a reachable host (e.g. http://100.x.y.z:18989 over
// Tailscale) before `cap sync`. Without it the bundled web build talks
// to localhost — fine in the simulator on a host with the daemon
// running, broken on a real device.
const apiHost = process.env.CAP_NEXUS_API ?? process.env.VITE_NEXUS_API;
const isCleartext = !!apiHost && apiHost.startsWith("http://");

const config: CapacitorConfig = {
  appId: "com.nexus.app",
  appName: "Nexus",
  webDir: "dist",
  server: {
    ...(apiHost ? { url: apiHost } : {}),
    ...(isCleartext ? { cleartext: true } : {}),
    androidScheme: "https",
  },
};

export default config;
