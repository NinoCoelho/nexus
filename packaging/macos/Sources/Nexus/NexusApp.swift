import SwiftUI
import AppKit
import ServiceManagement
import UserNotifications

@main
struct NexusApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate

    var body: some Scene {
        MenuBarExtra("Nexus", systemImage: delegate.controller.updateChecker.updateAvailable ? "circle.hexagongrid.fill.badge" : "circle.hexagongrid.fill") {
            MenuView()
                .environmentObject(delegate.controller)
                .environmentObject(delegate.controller.updateChecker)
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
    let updateChecker = UpdateChecker()
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
                updateChecker.configure(port: port)
            } catch {
                status = "Error: \(error.localizedDescription)"
            }
        }
    }

    func openInBrowser(port: Int? = nil) {
        guard let p = port ?? server.port else { return }
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
                updateChecker.configure(port: port)
            } catch {
                status = "Error: \(error.localizedDescription)"
            }
        }
    }

    func showPreferences() {
        prefsWindow.show { [weak self] _ in
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
    @EnvironmentObject var updateChecker: UpdateChecker

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
        updateMenu
        Divider()
        Button("Show Access Token…") { controller.showAccessToken() }
        Button("Show Logs") { controller.revealLogs() }
        Button("Open ~/.nexus") { controller.revealStateDir() }
        Divider()
        Button("Quit") { NSApp.terminate(nil) }
            .keyboardShortcut("q")
    }

    @ViewBuilder
    private var updateMenu: some View {
        if updateChecker.downloadState == "ready" {
            Button("Restart & Install Update") {
                updateChecker.installUpdate()
            }
        } else if updateChecker.downloadState == "downloading" {
            Text("Downloading update… \(Int(updateChecker.downloadProgress * 100))%")
        } else if updateChecker.updateAvailable {
            Button("● Update Available (v\(updateChecker.latestVersion ?? ""))") {
                updateChecker.startDownload()
            }
            Button("Skip This Version") {
                updateChecker.skipVersion()
            }
            Button("View Release Notes…") {
                updateChecker.openReleasePage()
            }
        } else {
            Button("Check for Updates…") {
                updateChecker.checkNow()
            }
        }
    }
}
