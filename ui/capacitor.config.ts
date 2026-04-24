import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "com.nexus.app",
  appName: "Nexus",
  webDir: "dist",
  server: {
    // Point this to your Tailscale IP or hostname.
    // Uncomment and edit when running against a remote backend:
    // url: "http://100.x.y.z:18989",
    // cleartext: true,
    androidScheme: "https",
  },
};

export default config;
