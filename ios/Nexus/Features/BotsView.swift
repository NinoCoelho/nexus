import SwiftUI

/// Bots roster (coordinator first, like the web sidebar). Tapping a bot goes
/// straight into its most recent chat (or a fresh one); the history/persona
/// screen is one tap away from inside the chat.
struct BotsView: View {
    @Environment(ServerStore.self) private var servers
    @Binding var openSessionId: String?

    @State private var bots: [Bot] = []
    @State private var sessions: [SessionSummary] = []
    @State private var error: String?
    @State private var deepLinkBot: Bot?

    /// Distinct navigation values so bot-tap (chat) and history don't collide.
    struct BotChatLaunch: Hashable { let bot: Bot }
    struct BotHistory: Hashable { let bot: Bot }

    var body: some View {
        NavigationStack {
            List {
                if let error {
                    Section { ErrorBanner(message: error) }
                }
                Section {
                    ForEach(bots) { bot in
                        NavigationLink(value: BotChatLaunch(bot: bot)) {
                            BotRow(bot: bot, chatCount: chatCount(for: bot))
                        }
                        .contextMenu {
                            NavigationLink(value: BotHistory(bot: bot)) {
                                Label("All chats & persona", systemImage: "list.bullet")
                            }
                        }
                    }
                } footer: {
                    if let status = servers.activeStatus {
                        Text("Connected to \(status.machineName ?? "server") · Nexus \(status.version ?? "")")
                    }
                }
            }
            .overlay {
                if bots.isEmpty && error == nil {
                    ProgressView().controlSize(.large)
                }
            }
            .navigationTitle("Bots")
            .refreshable { await load() }
            .navigationDestination(for: BotChatLaunch.self) { launch in
                ChatView(bot: launch.bot, sessionId: newestSession(for: launch.bot)?.id)
            }
            .navigationDestination(for: BotHistory.self) { entry in
                BotDetailView(bot: entry.bot, sessions: sessions(for: entry.bot))
            }
            .navigationDestination(for: SessionSummary.self) { session in
                ChatView(bot: bot(for: session), sessionId: session.id)
            }
            .navigationDestination(item: $deepLinkBot) { bot in
                ChatView(bot: bot, sessionId: openSessionId)
            }
            .onChange(of: openSessionId) { _, newValue in
                guard let newValue else { return }
                deepLinkBot = bots.first(where: { $0.isCoordinator })
                openSessionId = nil
            }
            .task { await load() }
        }
    }

    private func bot(for session: SessionSummary) -> Bot? {
        if let pid = session.projectId, pid != "main" {
            return bots.first(where: { $0.id == pid })
        }
        return bots.first(where: { $0.isCoordinator }) ?? bots.first(where: { $0.id == "main" })
    }

    /// The chat a bot tap lands in: the coordinator's designated master
    /// session when set, else the newest session.
    private func newestSession(for bot: Bot) -> SessionSummary? {
        let own = sessions(for: bot)
        if bot.isCoordinator, let sid = bot.sessionId,
           let master = own.first(where: { $0.id == sid }) {
            return master
        }
        return own.first
    }

    private func chatCount(for bot: Bot) -> Int {
        sessions(for: bot).count
    }

    /// Sessions belonging to a bot. The coordinator owns both unprojected
    /// chats (project_id NULL) and chats stamped "main".
    private func sessions(for bot: Bot) -> [SessionSummary] {
        if bot.isCoordinator || bot.id == "main" {
            var seen = Set<String>()
            return sessions.filter { session in
                let owns = session.projectId == nil
                    || session.projectId == "main"
                    || session.id == bot.sessionId
                guard owns, session.archived != true, seen.insert(session.id).inserted else {
                    return false
                }
                return true
            }
        }
        return sessions.filter { $0.projectId == bot.id && $0.archived != true }
    }

    private func load() async {
        guard let client = servers.client else { return }
        do {
            async let b = client.bots()
            async let s = client.sessions(limit: 200)
            bots = try await b
            sessions = try await s
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct BotRow: View {
    let bot: Bot
    let chatCount: Int

    var body: some View {
        HStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(bot.isCoordinator ? Theme.accent.opacity(0.25) : Theme.accent.opacity(0.12))
                    .frame(width: 40, height: 40)
                if bot.isCoordinator {
                    Image(systemName: "crown.fill")
                        .foregroundStyle(Theme.accent)
                } else {
                    Text(String(bot.name.prefix(1)).uppercased())
                        .font(.headline)
                        .foregroundStyle(Theme.accent)
                }
            }
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(bot.name).fontWeight(.semibold)
                    Text("@\(bot.handle)")
                        .font(.caption)
                        .foregroundStyle(Theme.subtleText)
                }
                if let desc = bot.description, !desc.isEmpty {
                    Text(desc)
                        .font(.caption)
                        .foregroundStyle(Theme.subtleText)
                        .lineLimit(1)
                }
            }
            Spacer()
            if chatCount > 0 {
                Text("\(chatCount)")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.subtleText)
            }
            Image(systemName: "chevron.right")
                .font(.caption)
                .foregroundStyle(Theme.subtleText.opacity(0.5))
        }
    }
}

// MARK: - Bot workspace (history + persona; reached from chat toolbar or long-press)

struct BotDetailView: View {
    let bot: Bot
    let sessions: [SessionSummary]

    @Environment(ServerStore.self) private var servers
    @State private var archived: Set<String> = []

    private var visibleSessions: [SessionSummary] {
        sessions.filter { !archived.contains($0.id) }
    }

    var body: some View {
        List {
            if let persona = bot.persona, !persona.isEmpty {
                Section("Persona") {
                    Text(persona)
                        .font(.callout)
                        .foregroundStyle(Theme.subtleText)
                }
            }
            Section("Chats") {
                if visibleSessions.isEmpty {
                    Text("No chats yet")
                        .foregroundStyle(Theme.subtleText)
                }
                ForEach(visibleSessions) { session in
                    NavigationLink(value: session) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(session.title.isEmpty ? "Untitled" : session.title)
                                .font(.body)
                                .lineLimit(1)
                            if let count = session.messageCount {
                                Text("\(count) messages")
                                    .font(.caption)
                                    .foregroundStyle(Theme.subtleText)
                            }
                        }
                    }
                    .swipeActions {
                        if bot.sessionId != session.id {
                            Button("Archive", systemImage: "archivebox") {
                                archived.insert(session.id)
                                Task { await archive(session) }
                            }
                            .tint(.orange)
                        }
                    }
                }
            }
            Section {
                NavigationLink {
                    ChatView(bot: bot, sessionId: nil)
                } label: {
                    Label("New chat", systemImage: "plus.circle")
                }
            }
        }
        .navigationTitle(bot.name)
        .navigationBarTitleDisplayMode(.inline)
    }

    private func archive(_ session: SessionSummary) async {
        guard let client = servers.client else { return }
        try? await client.archiveSession(id: session.id)
    }
}
