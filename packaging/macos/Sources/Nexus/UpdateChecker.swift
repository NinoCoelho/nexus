import Foundation
import AppKit
import UserNotifications

@MainActor
final class UpdateChecker: ObservableObject {
    @Published var latestVersion: String?
    @Published var currentVersion: String?
    @Published var updateAvailable: Bool = false
    @Published var downloadState: String = "idle"
    @Published var downloadProgress: Double = 0
    @Published var releaseNotes: String = ""
    @Published var htmlURL: String = ""

    private var timer: Timer?
    private var port: Int?

    func configure(port: Int) {
        self.port = port
        checkNow()
        timer = Timer.scheduledTimer(withTimeInterval: 4 * 3600, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in self.checkNow() }
        }
    }

    func checkNow() {
        guard let port else { return }
        let url = URL(string: "http://127.0.0.1:\(port)/update/check")!
        Task {
            guard let (data, _) = try? await URLSession.shared.data(from: url) else { return }
            guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
            self.currentVersion = json["current"] as? String
            self.latestVersion = json["latest"] as? String
            self.updateAvailable = json["update_available"] as? Bool ?? false
            self.releaseNotes = json["body"] as? String ?? ""
            self.htmlURL = json["html_url"] as? String ?? ""
            if self.updateAvailable {
                self.postNotification()
            }
        }
    }

    func startDownload() {
        guard let port else { return }
        downloadState = "downloading"
        downloadProgress = 0

        guard let url = URL(string: "http://127.0.0.1:\(port)/update/download") else { return }
        Task {
            let (_, response) = await URLSession.shared.data(from: url)
            if let http = response as? HTTPURLResponse, http.statusCode >= 400 {
                self.downloadState = "error"
                return
            }
            self.pollStatus(port: port)
        }
    }

    private func pollStatus(port: Int) {
        let url = URL(string: "http://127.0.0.1:\(port)/update/status")!
        Task {
            guard let (data, _) = try? await URLSession.shared.data(from: url) else { return }
            guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
            let state = json["state"] as? String ?? "idle"
            self.downloadState = state
            if state == "downloading" {
                self.downloadProgress = json["progress"] as? Double ?? 0
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                self.pollStatus(port: port)
            } else if state == "ready" {
                self.downloadProgress = 1.0
            }
        }
    }

    func installUpdate() {
        guard let port else { return }
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/update/install")!)
        request.httpMethod = "POST"
        URLSession.shared.dataTask(with: request).resume()
    }

    func skipVersion() {
        guard let port, let version = latestVersion else { return }
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/update/skip")!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body = ["version": version]
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        Task {
            _ = try? await URLSession.shared.data(for: request)
            self.updateAvailable = false
        }
    }

    func openReleasePage() {
        guard let url = URL(string: htmlURL) else { return }
        NSWorkspace.shared.open(url)
    }

    private func postNotification() {
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: .alert) { _, _ in }
        let content = UNMutableNotificationContent()
        content.title = "Nexus Update Available"
        content.body = "Version \(latestVersion ?? "") is ready to download."
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
        let request = UNNotificationRequest(identifier: "nexus-update", content: content, trigger: trigger)
        center.add(request)
    }
}
