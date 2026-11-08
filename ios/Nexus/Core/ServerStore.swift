import Foundation
import Observation

/// App-wide state: paired servers (tokens in Keychain, metadata in
/// UserDefaults) + the active one. Switching servers rebuilds the client.
@Observable
final class ServerStore {
    static let shared = ServerStore()

    private static let profilesKey = "nexus.servers.v1"
    private static let activeKey = "nexus.servers.active.v1"

    private(set) var profiles: [ServerProfile] = []
    private(set) var activeId: UUID?
    /// Resolved /status for the active server (connect-time handshake).
    var activeStatus: StatusInfo?
    var lastError: String?

    var active: ServerProfile? {
        profiles.first(where: { $0.id == activeId })
    }

    var client: NexusClient? {
        guard let active else { return nil }
        return NexusClient(profile: active)
    }

    private init() {
        load()
    }

    // MARK: - Persistence (metadata in UserDefaults, token in Keychain)

    private struct StoredProfile: Codable {
        var id: UUID
        var name: String
        var host: String
        var port: Int
        var tls: Bool?
    }

    private func load() {
        guard let data = UserDefaults.standard.data(forKey: Self.profilesKey),
              let stored = try? JSONDecoder().decode([StoredProfile].self, from: data) else { return }
        profiles = stored.compactMap { meta in
            guard let token = Keychain.getString(account: meta.id.uuidString) else { return nil }
            return ServerProfile(
                id: meta.id, name: meta.name, host: meta.host,
                port: meta.port, token: token, tls: meta.tls ?? false
            )
        }
        activeId = UUID(uuidString: UserDefaults.standard.string(forKey: Self.activeKey) ?? "")
            ?? profiles.first?.id
    }

    private func persist() {
        let stored = profiles.map {
            StoredProfile(id: $0.id, name: $0.name, host: $0.host, port: $0.port, tls: $0.tls)
        }
        UserDefaults.standard.set(try? JSONEncoder().encode(stored), forKey: Self.profilesKey)
        UserDefaults.standard.set(activeId?.uuidString, forKey: Self.activeKey)
    }

    // MARK: - Mutations

    @discardableResult
    func add(_ profile: ServerProfile, activate: Bool = true) -> ServerProfile {
        // Re-pairing the same host+port refreshes the token instead of duplicating.
        if let idx = profiles.firstIndex(where: { $0.host == profile.host && $0.port == profile.port }) {
            var merged = profile
            merged.id = profiles[idx].id
            profiles[idx] = merged
            Keychain.set(merged.token, account: merged.id.uuidString)
            if activate { activeId = merged.id; activeStatus = nil }
            persist()
            return merged
        }
        Keychain.set(profile.token, account: profile.id.uuidString)
        profiles.append(profile)
        if activate || profiles.count == 1 { activeId = profile.id; activeStatus = nil }
        persist()
        return profile
    }

    func remove(_ profile: ServerProfile) {
        profiles.removeAll(where: { $0.id == profile.id })
        Keychain.delete(account: profile.id.uuidString)
        if activeId == profile.id {
            activeId = profiles.first?.id
            activeStatus = nil
        }
        persist()
    }

    func activate(_ profile: ServerProfile) {
        guard profiles.contains(where: { $0.id == profile.id }) else { return }
        activeId = profile.id
        activeStatus = nil
        persist()
    }

    /// Refresh the /status handshake; returns nil on failure (error in lastError).
    @discardableResult
    func refreshStatus() async -> StatusInfo? {
        guard let client else { return nil }
        do {
            let info = try await client.status()
            lastError = nil
            activeStatus = info
            return info
        } catch {
            lastError = error.localizedDescription
            return nil
        }
    }
}
