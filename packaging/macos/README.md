# Nexus macOS host app

A native Swift menu bar app (`LSUIElement=true`, no Dock icon) that hosts the
bundled FastAPI server in a child process and dispatches native notifications
for HITL prompts. The UI itself runs in the user's default browser — the host
app is purely a daemon supervisor + notifier.

## Layout

```
macos/
  Package.swift            # SwiftPM manifest (executable target "Nexus")
  Sources/Nexus/
    NexusApp.swift         # @main entry — MenuBarExtra + AppController
    ServerController.swift # spawns Resources/python/bin/python3 bootstrap.py
    HitlNotifier.swift     # SSE listener + UNUserNotificationCenter dispatch
    PreferencesWindow.swift  # bind host / port (writes ~/.nexus/host.json)
    TokenDialog.swift      # show / copy the persistent access token
  Info.plist               # LSUIElement=YES; copied into the .app bundle
```

## Behaviour

* Server starts on app launch (auto-pick port by default; user-overridable in
  Preferences). Bootstrap.py writes the chosen port to `Resources/.port` so
  the Swift host doesn't have to parse logs.
* Browser opens automatically once `/health` returns 200.
* `HitlNotifier` subscribes to `/notifications/events` (SSE) and posts a
  native macOS notification for every `user_request`. Click → opens browser
  at the deeplink. Inline Approve/Deny actions on `confirm` POST `/respond`
  directly without a browser round-trip.
* `user_request_cancelled` and `user_request_auto` (YOLO) clear the matching
  banner so the Notification Center stays in sync with the daemon's bell.
* Reconnect-with-backoff (1 s → 10 s) — survives Restart Server / Preferences
  apply without losing future events.

## Build (standalone)

```bash
cd packaging/macos
swift build -c release --arch arm64
```

`packaging/build.sh` invokes this automatically and stitches the resulting
binary into `dist/Nexus.app/Contents/MacOS/Nexus`, then copies the staged
`Resources/` (Python, site-packages, ui, models) alongside it.

## Why SwiftPM and not an `.xcodeproj`?

Source-controlled, no IDE-generated noise, easy to diff. If you want Xcode
schemes for debugging, run `swift package generate-xcodeproj` locally — do
not commit the result.
