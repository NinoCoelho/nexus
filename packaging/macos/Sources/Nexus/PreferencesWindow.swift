import SwiftUI
import AppKit

/// Persisted bind preferences (host + port) — written to ~/.nexus/host.json.
/// bootstrap.py reads this on every launch.
struct HostSettings: Codable, Equatable {
    var host: String
    var port: Int  // 0 = auto-pick

    static let `default` = HostSettings(host: "127.0.0.1", port: 0)

    static var fileURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".nexus/host.json")
    }

    static func load() -> HostSettings {
        guard let data = try? Data(contentsOf: fileURL),
              let s = try? JSONDecoder().decode(HostSettings.self, from: data) else {
            return .default
        }
        return s
    }

    func save() throws {
        let dir = HostSettings.fileURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let data = try JSONEncoder().encode(self)
        try data.write(to: HostSettings.fileURL, options: .atomic)
    }
}

@MainActor
final class PreferencesWindowController {
    private var window: NSWindow?

    func show(onApply: @escaping (HostSettings) -> Void) {
        if let w = window {
            NSApp.activate(ignoringOtherApps: true)
            w.makeKeyAndOrderFront(nil)
            return
        }
        let view = PreferencesView(onApply: { [weak self] s in
            onApply(s)
            self?.window?.close()
        })
        let host = NSHostingController(rootView: view)
        let w = NSWindow(contentViewController: host)
        w.title = "Nexus Preferences"
        w.styleMask = [.titled, .closable, .miniaturizable]
        w.setContentSize(NSSize(width: 460, height: 320))
        w.isReleasedWhenClosed = false
        w.center()
        self.window = w
        NSApp.activate(ignoringOtherApps: true)
        w.makeKeyAndOrderFront(nil)
    }
}

private struct PreferencesView: View {
    @State private var host: String
    @State private var portMode: PortMode
    @State private var portText: String
    var onApply: (HostSettings) -> Void

    private enum PortMode: String, CaseIterable, Identifiable {
        case auto, fixed
        var id: String { rawValue }
    }

    init(onApply: @escaping (HostSettings) -> Void) {
        let s = HostSettings.load()
        _host = State(initialValue: s.host)
        _portMode = State(initialValue: s.port == 0 ? .auto : .fixed)
        _portText = State(initialValue: s.port == 0 ? "" : String(s.port))
        self.onApply = onApply
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Server Bind").font(.headline)

            VStack(alignment: .leading, spacing: 6) {
                Text("Host").font(.caption).foregroundStyle(.secondary)
                Picker("Host", selection: $host) {
                    Text("127.0.0.1 (this Mac only)").tag("127.0.0.1")
                    Text("0.0.0.0 (all network interfaces)").tag("0.0.0.0")
                }
                .labelsHidden()
                .pickerStyle(.radioGroup)

                if host == "0.0.0.0" {
                    Text("⚠️  Anyone on your network will be able to reach this server. Non-loopback requests must include the access token. Use only on trusted networks.")
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .padding(.top, 2)
                }
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("Port").font(.caption).foregroundStyle(.secondary)
                Picker("Port", selection: $portMode) {
                    Text("Auto-pick free port").tag(PortMode.auto)
                    Text("Specific port:").tag(PortMode.fixed)
                }
                .labelsHidden()
                .pickerStyle(.radioGroup)

                if portMode == .fixed {
                    TextField("e.g. 18989", text: $portText)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 140)
                }
            }

            Spacer()

            HStack {
                Spacer()
                Button("Cancel") { NSApp.keyWindow?.close() }
                    .keyboardShortcut(.cancelAction)
                Button("Apply & Restart") {
                    let port: Int
                    if portMode == .fixed, let p = Int(portText), p > 0, p < 65536 {
                        port = p
                    } else {
                        port = 0
                    }
                    let s = HostSettings(host: host, port: port)
                    try? s.save()
                    onApply(s)
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 460, height: 320)
    }
}
