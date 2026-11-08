import SwiftUI
import UserNotifications
import UIKit

struct SettingsView: View {
    @Binding var showPairing: Bool

    @Environment(ServerStore.self) private var servers
    @State private var pushEnabled = false

    var body: some View {
        NavigationStack {
            List {
                activeServerSection
                serversSection
                pushSection
                aboutSection
            }
            .navigationTitle("Settings")
        }
    }

    // MARK: - Active server

    private var activeServerSection: some View {
        Section("Server") {
            if let status = servers.activeStatus {
                LabeledContent("Machine", value: status.machineName ?? "—")
                LabeledContent("Address", value: servers.active.map { "\($0.host):\($0.port)" } ?? "—")
                if let version = status.version {
                    LabeledContent("Version", value: version)
                }
                if let ip = status.tailnetIp {
                    LabeledContent("Tailnet IP", value: ip)
                }
                featureRows(status.features)
            } else if let active = servers.active {
                LabeledContent("Address", value: "\(active.host):\(active.port)")
                if let error = servers.lastError {
                    ErrorBanner(message: error)
                }
                Button("Retry connection") {
                    Task { await servers.refreshStatus() }
                }
            }
        }
    }

    @ViewBuilder
    private func featureRows(_ features: ServerFeatures?) -> some View {
        if let features {
            LabeledContent("Browser control") {
                Text(features.browserRemoteControl == true ? "On" : "Off")
                    .foregroundStyle(features.browserRemoteControl == true ? .green : .secondary)
            }
            LabeledContent("Push (APNs)") {
                Text(features.pushApns == true ? "Deliverable" : "Needs server setup")
                    .foregroundStyle(features.pushApns == true ? .green : .orange)
            }
        }
    }

    // MARK: - Servers

    private var serversSection: some View {
        Section("Paired servers") {
            ForEach(servers.profiles) { profile in
                Button {
                    servers.activate(profile)
                    Task { await servers.refreshStatus() }
                } label: {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(profile.name).foregroundStyle(.primary)
                            Text("\(profile.host):\(profile.port)")
                                .font(.caption)
                                .foregroundStyle(Theme.subtleText)
                        }
                        Spacer()
                        if profile.id == servers.activeId {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(Theme.accent)
                        }
                    }
                }
            }
            .onDelete { indexSet in
                for index in indexSet {
                    if let profile = servers.profiles[safe: index] {
                        servers.remove(profile)
                    }
                }
            }
            Button {
                showPairing = true
            } label: {
                Label("Pair another server", systemImage: "qrcode")
            }
        }
    }

    // MARK: - Push

    private var pushSection: some View {
        Section {
            Toggle("HITL notifications", isOn: $pushEnabled)
                .onChange(of: pushEnabled) { _, enabled in
                    if enabled {
                        Task { await registerForPush() }
                    }
                }
        } footer: {
            Text("Ask-your-user prompts arrive as push notifications when the app is closed. Needs [push].apns configured on the server.")
        }
        .onAppear {
            pushEnabled = UserDefaults.standard.bool(forKey: "nexus.push.enabled")
        }
    }

    private func registerForPush() async {
        UserDefaults.standard.set(pushEnabled, forKey: "nexus.push.enabled")
        guard pushEnabled else { return }
        let center = UNUserNotificationCenter.current()
        guard let granted = try? await center.requestAuthorization(options: [.alert, .sound, .badge]), granted else {
            pushEnabled = false
            UserDefaults.standard.set(false, forKey: "nexus.push.enabled")
            return
        }
        await MainActor.run {
            UIApplication.shared.registerForRemoteNotifications()
        }
    }

    // MARK: - About

    private var aboutSection: some View {
        Section("About") {
            LabeledContent("App", value: "Nexus \(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0")")
            LabeledContent("Transport", value: "Tailscale (WireGuard) + token")
        }
    }
}

extension Array {
    subscript(safe index: Int) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}
