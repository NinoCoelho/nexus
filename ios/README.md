# Nexus for iOS

A native SwiftUI client for your Nexus server, designed for **tailnet access**
(Tailscale): your phone and your Mac are on the same private network, the
server binds only its Tailscale IP, and the app authenticates with a
pairing token delivered by QR code.

## What it does

- **Bots & chats** — full roster (coordinator + every bot), read/send with
  live streaming deltas, thinking/tool activity, message queueing while a
  turn is running, and human-in-the-loop prompts (confirm / choice / text /
  form, dismissible without answering) exactly like the web UI.
- **Chat UX** — the transcript is a full (non-lazy) stack that opens pinned
  to the newest message (the content is revealed only after the initial
  scroll lands — no blank viewport, no top-of-transcript flash). Auto-scroll
  follows the stream only while you are at the bottom; scrolling up releases
  it, and a floating ⌄ button returns to the latest message. Long-press any
  message to copy it (with success haptic). Errors surface as a dismissible
  toast even when history is already on screen.
- **In-chat search** — 🔍 in the toolbar opens a local search over the
  in-memory transcript: case- and diacritic-insensitive filter, a hit
  counter (`3/12`), prev/next arrows that scroll to each match, and the
  term highlighted inside the bubbles (markdown-aware).
- **Attachments** — camera, photo library, Files picker, or clipboard paste
  (screenshot, video, files; a mixed image+text clipboard stages the image
  and keeps its text for the draft). Staged files upload to the vault when
  the turn is sent and the message references them by path.
- **Browser** — watch every bot's dedicated Chrome live (CDP screencast over
  SSE) and drive it: tap, scroll, type, special keys, navigate. Requires
  `[browser] remote_control = true` on the server (off by default).
- **Vault** (read-only) — browse the file tree, read markdown notes, full-text
  search.
- **Push** — `ask_user` prompts arrive as APNs notifications when the app is
  closed (needs `[push].apns` configured server-side).

## Roadmap (planned, not yet implemented)

Documented so they aren't mistaken for shipped features:

- **Rich content-part rendering** — inline images, PDFs and audio inside
  chat bubbles. Multi-part messages currently render as plain
  `📎 <path>` text lines; the parts model exists server-side.
- **Voice** — speech input and spoken playback of messages.

## Setup (server side, once)

1. Bind Nexus to the tailnet — in `~/.nexus/config.toml`:

   ```toml
   [server]
   bind = "tailscale"       # listens only on the machine's 100.x Tailscale IP

   [browser]
   remote_control = true    # optional: let the phone drive bots' browsers
   ```

2. Restart: `nexus daemon restart`
3. Show the pairing QR: `nexus pair` (terminal) or **Settings → Native app
   pairing** in the web UI.

The phone must run the Tailscale app and be signed into the same tailnet.

## Pairing (phone)

Scan the QR with the iOS camera (or in-app scanner) → the `nexus://pair`
deep link opens the app → confirm the machine name → done. The token is
stored in the Keychain. `nexus pair --rotate` (or the "Revoke paired
devices" button) invalidates every paired phone.

## Security model

| Layer | Provided by |
|---|---|
| Network admission + transport encryption | Tailscale (WireGuard) — the bind is unreachable off the tailnet |
| Authentication | Long-lived pairing token (`~/.nexus/client_token`, Bearer header) |
| Revocation | `nexus pair --rotate` — immediate, no restart |
| Browser control | Opt-in `[browser] remote_control` flag |

The token is only ever served to loopback requests (`GET /pair`).

## Building

```bash
open Nexus.xcodeproj   # Xcode 26+, iOS 17 deployment target
```

Select your device (needs a signing team for device installs) or the
simulator and run. No third-party package dependencies.

```
Nexus/
├── NexusApp.swift        # entry, APNs registration, deep links
├── Core/
│   ├── Models.swift      # API DTOs (bots, sessions, stream events, HITL…)
│   ├── SSE.swift         # SSE line parser + URLSession.bytes streaming
│   ├── NexusClient.swift # async API client (Bearer auth)
│   ├── ServerStore.swift # paired servers (Keychain + UserDefaults)
│   └── Keychain.swift
├── Features/
│   ├── RootView.swift    # tabs / pairing gate
│   ├── PairingView.swift # QR scanner + manual entry + confirm
│   ├── BotsView.swift    # roster → bot workspace → chats
│   ├── ChatView.swift    # transcript, streaming, search, queue chips, HITL sheets
│   ├── MarkdownText.swift# dependency-free markdown renderer
│   ├── BrowserView.swift # live screencast + touch control
│   ├── VaultView.swift   # tree / reader / search
│   └── SettingsView.swift
└── Support/Theme.swift
```

### Simulator testing

The simulator shares the Mac's network — pair against the loopback server
by manually entering host `127.0.0.1`, port `18989`, and the token from
`nexus pair`. (The daemon binds loopback by default, so this works without
any config change; the camera scanner needs a real device.)

## APNs server setup (optional, for closed-app notifications)

In `~/.nexus/config.toml`:

```toml
[push.apns]
key_path = "~/keys/AuthKey_ABC123.p8"   # from apple.com developer account
key_id = "ABC123"
team_id = "TEAM12345"
bundle_id = "com.nexus.app"
environment = "sandbox"                  # "production" for TestFlight/App Store
```

Restart the daemon. The app registers its device token on launch; the
server fans `user_request` prompts out to every registered device.
