# Nexus macOS host app

A native Swift menu bar app that hosts the bundled FastAPI server in a child
process and renders the React UI in a `WKWebView`.

## Layout

```
macos/
  Package.swift            # SwiftPM manifest (executable target "Nexus")
  Sources/Nexus/
    NexusApp.swift         # @main entry — MenuBarExtra + window
    ServerController.swift # spawns Resources/python/bin/python3 bootstrap.py
    WebWindowController.swift  # NSWindow hosting WKWebView
  Info.plist               # LSUIElement=YES; copied into the .app bundle
```

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
