// Nexus service worker — handles Web Push delivery for HITL prompts.
//
// The page registers this at "/" scope. The browser keeps the SW
// alive (or restarts it on push) so OS-level notifications fire even
// when no Nexus tab is open. We deliberately don't cache anything —
// this isn't a PWA, just a push handler.

// API base used by the install/click handlers to call the backend.
// Same-origin assumption: the backend serves the bundled UI from
// itself, so relative URLs work. Capacitor / dev-mode users override
// at registration time via VITE_NEXUS_API in the page hook, not here.
const API_BASE = "";

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// Push payload shape (see agent/src/nexus/push/sender.py):
//   { title, body, session_id, request_id, kind, timeout_seconds }
self.addEventListener("push", (event) => {
  if (!event.data) return;
  let payload;
  try {
    payload = event.data.json();
  } catch {
    payload = { title: "Nexus", body: event.data.text() };
  }
  const title = payload.title || "Nexus needs input";
  const body = payload.body || "";
  const requestId = payload.request_id || "";
  const sessionId = payload.session_id || "";
  const kind = payload.kind || "confirm";

  event.waitUntil(
    (async () => {
      // If a Nexus tab is already focused, the in-app modal will show.
      // Don't double-notify — but DO post a message so the focused tab
      // can hop the queue to this specific request_id.
      const allClients = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      const visible = allClients.find((c) => c.visibilityState === "visible");
      if (visible) {
        try {
          visible.postMessage({
            type: "nx-hitl-incoming",
            request_id: requestId,
            session_id: sessionId,
          });
        } catch { /* ignore */ }
        return;
      }
      await self.registration.showNotification(title, {
        body,
        tag: requestId || "nexus-hitl",
        data: { request_id: requestId, session_id: sessionId, kind },
        requireInteraction: kind !== "confirm",
        icon: "/icons/nexus-192.svg",
        badge: "/icons/nexus-192.svg",
      });
    })(),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const url = "/?respond=" + encodeURIComponent(data.request_id || "")
    + "&session=" + encodeURIComponent(data.session_id || "");

  event.waitUntil(
    (async () => {
      const all = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      // Reuse an existing Nexus tab if one is open — focus it and
      // tell it which request to surface.
      for (const client of all) {
        const u = new URL(client.url);
        if (u.origin === self.location.origin) {
          try {
            client.postMessage({
              type: "nx-hitl-incoming",
              request_id: data.request_id,
              session_id: data.session_id,
            });
            return client.focus();
          } catch { /* fall through to openWindow */ }
        }
      }
      return self.clients.openWindow(url);
    })(),
  );
});

// The browser may rotate the subscription endpoint; resubscribe and
// re-register so we keep delivering.
self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil(
    (async () => {
      try {
        const res = await fetch(API_BASE + "/push/vapid-public-key");
        const { public_key } = await res.json();
        const sub = await self.registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(public_key),
        });
        await fetch(API_BASE + "/push/subscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(sub.toJSON()),
        });
      } catch {
        // Network blip / VAPID changed — best-effort. Page hook will
        // resubscribe on next visit.
      }
    })(),
  );
});

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  const out = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) out[i] = rawData.charCodeAt(i);
  return out;
}
