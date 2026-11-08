import SwiftUI

/// Top-level navigation. No active server → pairing; otherwise the four
/// main surfaces. Deep links (nexus://pair, push taps) route in here.
struct RootView: View {
    @Environment(ServerStore.self) private var servers
    @State private var showPairing = false
    @State private var deepLinkProfile: ServerProfile?
    @State private var selectedTab: Tab = .bots
    @State private var openSessionId: String?

    enum Tab: Hashable {
        case bots, browser, vault, settings
    }

    var body: some View {
        Group {
            if servers.client != nil {
                TabView(selection: $selectedTab) {
                    BotsView(openSessionId: $openSessionId)
                        .tabItem { Label("Bots", systemImage: "person.2") }
                        .tag(Tab.bots)
                    BrowserView()
                        .tabItem { Label("Browser", systemImage: "safari") }
                        .tag(Tab.browser)
                    VaultView()
                        .tabItem { Label("Vault", systemImage: "archivebox") }
                        .tag(Tab.vault)
                    SettingsView(showPairing: $showPairing)
                        .tabItem { Label("Settings", systemImage: "gearshape") }
                        .tag(Tab.settings)
                }
            } else {
                emptyState
            }
        }
        .sheet(isPresented: $showPairing) {
            PairingView(scannedProfile: deepLinkProfile) { profile in
                _ = servers.add(profile)
                showPairing = false
                deepLinkProfile = nil
                Task { await servers.refreshStatus() }
            }
            .interactiveDismissDisabled(servers.profiles.isEmpty)
        }
        .onReceive(NotificationCenter.default.publisher(for: .pairDeepLink)) { note in
            if let profile = note.userInfo?["profile"] as? ServerProfile {
                deepLinkProfile = profile
                showPairing = true
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .openSession)) { note in
            if let sid = note.userInfo?["session_id"] as? String {
                openSessionId = sid
                selectedTab = .bots
            }
        }
        .task {
            if servers.client != nil {
                await servers.refreshStatus()
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 20) {
            Spacer()
            Image(systemName: "network")
                .font(.system(size: 52))
                .foregroundStyle(Theme.accent)
            Text("No server paired")
                .font(.title3.bold())
            Text("On your Mac: Settings → Native app pairing\n(or run `nexus pair`), then scan the QR.")
                .font(.subheadline)
                .foregroundStyle(Theme.subtleText)
                .multilineTextAlignment(.center)
            Button("Pair a server") { showPairing = true }
                .buttonStyle(.borderedProminent)
            if let error = servers.lastError {
                ErrorBanner(message: error)
                    .padding(.horizontal)
            }
            Text("build \(BuildInfo.stamp)")
                .font(.caption2)
                .foregroundStyle(Theme.subtleText.opacity(0.6))
            Spacer()
        }
        .padding()
    }
}
