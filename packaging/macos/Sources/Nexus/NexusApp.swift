import SwiftUI
import AppKit

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
    private let server = ServerController()
    private var window: WebWindowController?

    func start() {
        Task {
            do {
                try server.launch()
                status = "Waiting for server…"
                let port = try await server.waitForReady(timeout: 30)
                status = "Running on :\(port)"
                openWindow(port: port)
            } catch {
                status = "Error: \(error.localizedDescription)"
            }
        }
    }

    func openWindow(port: Int? = nil) {
        let p = port ?? server.port ?? 18989
        if window == nil {
            window = WebWindowController(url: URL(string: "http://127.0.0.1:\(p)/")!)
        }
        window?.show()
    }

    func restartServer() {
        Task {
            status = "Restarting…"
            server.terminate()
            do {
                try server.launch()
                let port = try await server.waitForReady(timeout: 30)
                status = "Running on :\(port)"
                window?.reload(url: URL(string: "http://127.0.0.1:\(port)/")!)
            } catch {
                status = "Error: \(error.localizedDescription)"
            }
        }
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

    func shutdown() {
        server.terminate()
    }
}

struct MenuView: View {
    @EnvironmentObject var controller: AppController

    var body: some View {
        Text(controller.status)
            .font(.caption)
        Divider()
        Button("Open Nexus") { controller.openWindow() }
            .keyboardShortcut("o")
        Button("Restart Server") { controller.restartServer() }
        Divider()
        Button("Show Logs") { controller.revealLogs() }
        Button("Open ~/.nexus") { controller.revealStateDir() }
        Divider()
        Button("Quit") { NSApp.terminate(nil) }
            .keyboardShortcut("q")
    }
}
