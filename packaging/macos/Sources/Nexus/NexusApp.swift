import SwiftUI
import AppKit
import ServiceManagement

@main
struct NexusApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate

    var body: some Scene {
        MenuBarExtra("Nexus", systemImage: "circle.hexagongrid.fill") {
            MenuView()
                .environmentObject(delegate.controller)
        }
        .menuBarExtraStyle(.menu)
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let controller = AppController()

    func applicationDidFinishLaunching(_ notification: Notification) {
        controller.start()
    }

    func applicationWillTerminate(_ notification: Notification) {
        controller.shutdown()
    }
}

@MainActor
final class AppController: ObservableObject {
    @Published var status: String = "Starting…"
    @Published var startAtLogin: Bool = SMAppService.mainApp.status == .enabled
    private let server = ServerController()
    private let prefsWindow = PreferencesWindowController()
    private let notifier = HitlNotifier()

    func start() {
        Task {
            do {
                try server.launch()
                status = "Waiting for server…"
                let port = try await server.waitForReady(timeout: 60)
                status = "Running on \(server.bindHost):\(port)"
                openInBrowser(port: port)
                notifier.start(server: server)
            } catch {
                status = "Error: \(error.localizedDescription)"
            }
        }
    }

    func openInBrowser(port: Int? = nil) {
        guard let p = port ?? server.port else { return }
        // Use loopback in the browser even when bound to 0.0.0.0 — opening
        // 0.0.0.0 in a browser doesn't work and the local user has loopback
        // exemption from the auth check.
        let urlString = "http://127.0.0.1:\(p)/"
        guard let url = URL(string: urlString) else { return }
        NSWorkspace.shared.open(url)
    }

    func restartServer() {
        Task {
            status = "Restarting…"
            notifier.stop()
            server.terminate()
            do {
                try server.launch()
                let port = try await server.waitForReady(timeout: 60)
                status = "Running on \(server.bindHost):\(port)"
                openInBrowser(port: port)
                notifier.start(server: server)
            } catch {
                status = "Error: \(error.localizedDescription)"
            }
        }
    }

    func showPreferences() {
        prefsWindow.show { [weak self] _ in
            // host.json was just written; restart so bootstrap.py picks it up.
            self?.restartServer()
        }
    }

    func toggleStartAtLogin() {
        do {
            if SMAppService.mainApp.status == .enabled {
                try SMAppService.mainApp.unregister()
            } else {
                try SMAppService.mainApp.register()
            }
        } catch {
            NSAlert(error: error).runModal()
        }
        startAtLogin = SMAppService.mainApp.status == .enabled
    }

    func revealStateDir() {
        let url = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".nexus")
        NSWorkspace.shared.open(url)
    }

    func revealLogs() {
        let url = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/Nexus")
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        NSWorkspace.shared.open(url)
    }

    func showAccessToken() {
        TokenDialog.showCurrentToken()
    }

    func shutdown() {
        notifier.stop()
        server.terminate()
    }
}

struct MenuView: View {
    @EnvironmentObject var controller: AppController

    var body: some View {
        Text(controller.status)
            .font(.caption)
        Divider()
        Button("Open Nexus") { controller.openInBrowser() }
            .keyboardShortcut("o")
        Divider()
        Button("Restart Server") { controller.restartServer() }
        Button("Preferences…") { controller.showPreferences() }
            .keyboardShortcut(",")
        Button(controller.startAtLogin ? "✓ Start at Login" : "Start at Login") {
            controller.toggleStartAtLogin()
        }
        Divider()
        Button("Show Access Token…") { controller.showAccessToken() }
        Button("Show Logs") { controller.revealLogs() }
        Button("Open ~/.nexus") { controller.revealStateDir() }
        Divider()
        Button("Quit") { NSApp.terminate(nil) }
            .keyboardShortcut("q")
    }
}
