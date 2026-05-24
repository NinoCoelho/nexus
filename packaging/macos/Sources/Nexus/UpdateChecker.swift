import Foundation
import AppKit

@MainActor
final class UpdateChecker: ObservableObject {
    @Published var latestVersion: String?
    @Published var currentVersion: String?
    @Published var updateAvailable: Bool = false
    @Published var downloadState: String = "idle"  // idle, downloading, ready, error
    @Published var downloadProgress: Double = 0
    @Published var releaseNotes: String = ""
    @Published var htmlURL: String = ""

    private var timer: Timer?
    private var port: Int?

    func configure(port: Int) {
        self.port = port
        checkNow()
        timer = Timer.scheduledTimer(withTimeInterval: 4 * 3600, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.checkNow() }
        }
    }

    func checkNow() {
        guard let port else { return }
        let url = URL(string: "http://127.0.0.1:\(port)/update/check")!
        let task = URLSession.shared.dataTask(with: url) { [weak self] data, _, error in
            guard let self, let data, error == nil else { return }
            do {
                let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
                DispatchQueue.main.async {
                    self.currentVersion = json?["current"] as? String
                    self.latestVersion = json?["latest"] as? String
                    self.updateAvailable = json?["update_available"] as? Bool ?? false
                    self.releaseNotes = json?["body"] as? String ?? ""
                    self.htmlURL = json?["html_url"] as? String ?? ""
                    if self.updateAvailable {
                        self.postNotification()
                    }
                }
            } catch {}
        }
        task.resume()
    }

    func startDownload() {
        guard let port else { return }
        downloadState = "downloading"
        downloadProgress = 0

        guard let url = URL(string: "http://127.0.0.1:\(port)/update/download") else { return }
        let task = URLSession.shared.dataTask(with: url) { [weak self] data, _, error in
            guard let self else { return }
            DispatchQueue.main.async {
                if error != nil {
                    self.downloadState = "error"
                    return
                }
                self.pollStatus(port: port)
            }
        }
        task.resume()
    }

    private func pollStatus(port: Int) {
        let url = URL(string: "http://127.0.0.1:\(port)/update/status")!
        let task = URLSession.shared.dataTask(with: url) { [weak self] data, _, _ in
            guard let self, let data else { return }
            do {
                let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
                let state = json?["state"] as? String ?? "idle"
                DispatchQueue.main.async {
                    self.downloadState = state
                    if state == "downloading" {
                        self.downloadProgress = json?["progress"] as? Double ?? 0
                        DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                            self.pollStatus(port: port)
                        }
                    } else if state == "ready" {
                        self.downloadProgress = 1.0
                    }
                }
            } catch {}
        }
        task.resume()
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
        URLSession.shared.dataTask(with: request) { [weak self] _, _, _ in
            DispatchQueue.main.async {
                self?.updateAvailable = false
            }
        }.resume()
    }

    func openReleasePage() {
        guard let url = URL(string: htmlURL) else { return }
        NSWorkspace.shared.open(url)
    }

    private func postNotification() {
        let content = UNUserNotificationCenter.current()
        content.requestAuthorization(options: .alert) { _, _ in }
        let req = UNMutableNotificationContent()
        req.title = "Nexus Update Available"
        req.body = "Version \(latestVersion ?? "") is ready to download."
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
        let request = UNNotificationRequest(identifier: "nexus-update", content: req, trigger: trigger)
        UNUserNotificationCenter.current().add(request)
    }
}
