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

## Code signing, notarization, and packaging

The build script (`packaging/build.sh`) can sign the app with a Developer ID
certificate, submit it for Apple notarization, and produce a compressed `.pkg`
installer. Configuration lives in `packaging/build.conf` (git-ignored; see
`build.conf.example`).

### Prerequisites

You need an active [Apple Developer Program](https://developer.apple.com/programs/)
membership.

### Step 1 — Generate a Certificate Signing Request (CSR)

1. Open **Keychain Access** (Spotlight → Keychain Access).
2. Menu bar: **Keychain Access → Certificate Assistant → Request a Certificate
   from a Certificate Authority...**
3. Enter your email address and a common name (e.g. "Nexus").
4. Leave the CA email address blank.
5. Select **Saved to disk** and click Continue.
6. Save the `.certSigningRequest` file — you'll upload it in the next step.

You only need **one** CSR. Both the Application and Installer certificates can
use the same request.

### Step 2 — Create the Developer ID certificates

Go to [Certificates, Identifiers & Profiles](https://developer.apple.com/account/resources/certificates/list)
on the Apple Developer portal. You need **two** certificates:

#### Developer ID Application (signs the .app)

1. Click **+** → under **Services**, select **Developer ID Application
   Certificate**.
2. Choose **Developer ID G2** as the intermediary (current standard).
3. Upload the CSR file from Step 1.
4. Download the `.cer` file.
5. Double-click it (or `security import developerID_application.cer -k
   ~/Library/Keychains/login.keychain-db`) to install into Keychain.

#### Developer ID Installer (signs the .pkg)

1. Click **+** → under **Services**, select **Developer ID Installer
   Certificate**.
2. Choose **Developer ID G2** as the intermediary.
3. Upload the **same** CSR file.
4. Download the `.cer` file.
5. Double-click it to install into Keychain.

Verify both are installed:

```bash
security find-identity -v -p codesigning   # lists the Application identity
security find-identity -v                  # lists both identities
```

### Step 3 — Create an app-specific password (for notarization)

1. Go to [appleid.apple.com](https://appleid.apple.com) → sign in.
2. Under **Sign-In and Security**, enable **Two-Factor Authentication** if not
   already on.
3. Click **App-Specific Passwords** → generate one (label it e.g. "Nexus
   Notarization").
4. Copy the `xxxx-xxxx-xxxx-xxxx` password.

### Step 4 — Configure build.conf

```bash
cp packaging/build.conf.example packaging/build.conf
```

Edit `packaging/build.conf`:

```bash
SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
NOTARIZE=1
NOTARY_APPLE_ID="you@icloud.com"
NOTARY_TEAM_ID="TEAMID"
NOTARY_PASSWORD="xxxx-xxxx-xxxx-xxxx"
NEXUS_INSTALLER_IDENTITY="Developer ID Installer: Your Name (TEAMID)"
```

### Step 5 — Build

```bash
packaging/build.sh
```

This produces:

- `dist/Nexus.app` — signed, notarized, stapled (1.7 GB)
- `dist/Nexus.pkg` — signed installer, compressed (~600 MB)

The `.pkg` installs into `/Applications`. Users double-click it to install.
