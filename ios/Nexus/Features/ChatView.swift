import SwiftUI

/// One chat: transcript + composer with live streaming, message queueing,
/// and human-in-the-loop prompts.
struct ChatView: View {
    let bot: Bot?
    /// nil → new chat; the session is created by the first POST /chat/stream.
    let sessionId: String?

    @Environment(ServerStore.self) private var servers
    @Environment(\.scenePhase) private var scenePhase

    @State private var resolvedSessionId: String?
    @State private var messages: [ChatMessage] = []
    @State private var title = ""
    @State private var input = ""
    @State private var streamingText = ""
    @State private var thinkingText = ""
    @State private var toolActivity: String?
    @State private var streamingBot: String?
    @State private var isStreaming = false
    @State private var queuedMessages: [QueuedMessage] = []
    @State private var pendingRequest: PendingRequest?
    @State private var formValues: [String: String] = [:]
    @State private var error: String?
    @State private var loading = true
    @State private var eventsTask: Task<Void, Never>?
    @State private var streamTask: Task<Void, Never>?
    @State private var autoScroll = true
    @State private var lastSentText = ""

    struct QueuedMessage: Identifiable, Equatable {
        let id: String
        var text: String
    }

    private var effectiveSessionId: String? { resolvedSessionId ?? sessionId }

    var body: some View {
        VStack(spacing: 0) {
            if let error, messages.isEmpty {
                ErrorBanner(message: error)
            }
            transcript
            if !queuedMessages.isEmpty {
                queueStrip
            }
            if let pendingRequest {
                HitlSheet(request: pendingRequest) { answer in
                    respond(answer)
                } onCancel: {
                    Task { await cancelPrompt() }
                }
                .padding(8)
                .background(Theme.panel)
            }
            composer
        }
        .navigationTitle(title.isEmpty ? (bot?.name ?? "Chat") : title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let bot {
                ToolbarItem(placement: .topBarTrailing) {
                    NavigationLink(value: BotsView.BotHistory(bot: bot)) {
                        Image(systemName: "clock.arrow.circlepath")
                    }
                    .accessibilityLabel("All chats")
                }
            }
            if isStreaming {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Stop") { stopStreaming() }
                        .foregroundStyle(.red)
                }
            }
        }
        .task {
            await initialLoad()
            startEventsChannel()
        }
        .onDisappear {
            eventsTask?.cancel()
            streamTask?.cancel()
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                Task { await resyncAfterBackground() }
            }
        }
    }

    // MARK: - Transcript

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 10) {
                    if loading {
                        ProgressView().padding(.top, 40)
                    }
                    ForEach(messages) { message in
                        MessageBubble(message: message)
                            .id(message.seq)
                    }
                    if !thinkingText.isEmpty {
                        ThinkingBlock(text: thinkingText).id("thinking")
                    }
                    if isStreaming {
                        VStack(alignment: .leading, spacing: 6) {
                            if let streamingBot {
                                Badge(text: "@\(streamingBot)")
                            }
                            if streamingText.isEmpty {
                                TypingDots()
                            } else {
                                MarkdownText(markdown: streamingText)
                            }
                        }
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Theme.bubbleBot, in: RoundedRectangle(cornerRadius: 14))
                        .id("streaming")
                    }
                    if let toolActivity {
                        Label(toolActivity, systemImage: "wrench.and.screwdriver")
                            .font(.caption)
                            .foregroundStyle(Theme.subtleText)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .id("tool")
                    }
                }
                .padding(12)
            }
            .defaultScrollAnchor(.bottom)
            .onChange(of: messages.count) { _, _ in
                if autoScroll { withAnimation { proxy.scrollTo("streaming", anchor: .bottom) } }
            }
            .onChange(of: streamingText) { _, _ in
                if autoScroll { proxy.scrollTo("streaming", anchor: .bottom) }
            }
            .onTapGesture { hideKeyboard() }
        }
    }

    private var queueStrip: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(queuedMessages) { q in
                    HStack(spacing: 4) {
                        Image(systemName: "clock")
                            .font(.caption2)
                        Text(q.text)
                            .font(.caption)
                            .lineLimit(1)
                    }
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Theme.accent.opacity(0.12), in: Capsule())
                }
            }
            .padding(.horizontal)
            .padding(.vertical, 4)
        }
        .frame(height: 32)
        .background(Theme.panel)
    }

    // MARK: - Composer

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField(isStreaming ? "Queue a message…" : "Message", text: $input, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(1...5)
                .padding(10)
                .background(Theme.panel, in: RoundedRectangle(cornerRadius: 18))
                .onSubmit { send() }
                .disabled(pendingRequest != nil)
            Button {
                send()
            } label: {
                Image(systemName: isStreaming ? "plus.circle" : "arrow.up.circle.fill")
                    .font(.system(size: 30))
                    .foregroundStyle(input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? Theme.subtleText : Theme.accent)
            }
            .disabled(input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(.bar)
    }

    // MARK: - Data flow

    private func initialLoad() async {
        guard let client = servers.client, let sid = effectiveSessionId else {
            loading = false
            return
        }
        do {
            let session = try await client.session(sid)
            messages = session.messages
            title = session.title
            loading = false
            error = nil
            if let pending = try await client.pendingRequest(sessionId: sid) {
                self.pendingRequest = pending
                seedFormValues(pending)
            }
            // A turn started on another surface (browser, extension) isn't
            // persisted until it settles — the snapshot above can't show it.
            // Attach to the live turn stream (which replays from the turn's
            // start) so cross-device progress appears immediately.
            if !isStreaming, let active = try? await client.activeTurn(sessionId: sid), active {
                attachTurnStream(client: client, sessionId: sid)
            }
        } catch {
            self.error = error.localizedDescription
            loading = false
        }
    }

    private func send() {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, let client = servers.client, !isStreaming else { return }
        input = ""
        // Optimistic user bubble (new chat: becomes seq 0; server truth replaces it on done).
        let optimistic = ChatMessage(
            seq: (messages.last?.seq ?? 0) + 1,
            role: "user",
            content: .text(text),
            toolCalls: nil, toolCallId: nil, createdAt: nil,
            feedback: nil, pinned: nil, bot: nil
        )
        messages.append(optimistic)
        startStream(client: client, message: text, sessionId: effectiveSessionId)
    }

    private func startStream(client: NexusClient, message: String, sessionId: String?) {
        isStreaming = true
        streamingText = ""
        thinkingText = ""
        toolActivity = nil
        streamingBot = nil
        lastSentText = message
        streamTask = Task {
            do {
                for try await event in client.chatStream(sessionId: sessionId, message: message, projectId: bot.map(\.id)) {
                    await MainActor.run { handle(event) }
                }
                // Server closed the stream — settle whatever we have.
                await MainActor.run { settleStream() }
            } catch {
                await MainActor.run {
                    self.error = error.localizedDescription
                    settleStream()
                }
            }
        }
    }

    /// Attach to the running turn's stream — used after a queued ack (the
    /// injection + chained reply flow there) and after backgrounding.
    private func attachTurnStream(client: NexusClient, sessionId: String) {
        isStreaming = true
        streamTask = Task {
            do {
                for try await event in client.turnStream(sessionId: sessionId) {
                    await MainActor.run { handle(event) }
                }
                await MainActor.run { settleStream() }
            } catch {
                await MainActor.run { settleStream() }
            }
        }
    }

    @MainActor
    private func handle(_ event: ChatStreamEvent) {
        switch event {
        case .delta(let text):
            streamingText += text
        case .thinking(let text):
            thinkingText += text
        case .tool(let name, let preview):
            var line = name ?? "tool"
            if let preview { line += " · " + String(preview.prefix(60)) }
            toolActivity = line
        case .done(let payload):
            if let sid = payload.sessionId { resolvedSessionId = sid }
            settleStream(reply: payload.reply ?? streamingText)
        case .error(let err):
            self.error = err.detail ?? "turn failed"
            settleStream()
        case .limitReached:
            self.error = "Context limit reached — compact this chat from the web UI."
        case .reconnecting:
            toolActivity = "reconnecting…"
        case .queued(let qid):
            // This POST was accepted into the running turn's queue — attach
            // to the turn stream to watch the injection + chained reply.
            queuedMessages.append(QueuedMessage(id: qid, text: lastSentText))
            settleStream()
            if let sid = effectiveSessionId, let client = servers.client {
                attachTurnStream(client: client, sessionId: sid)
            }
        case .userEnqueued(let q):
            if let qid = q.qid, let text = q.text {
                queuedMessages.append(QueuedMessage(id: qid, text: text))
            }
        case .userInjected(let q):
            if let qid = q.qid {
                queuedMessages.removeAll(where: { $0.id == qid })
            }
        case .queueRemoved(let qid):
            queuedMessages.removeAll(where: { $0.id == qid })
        case .turnSettled:
            settleStream()
        case .turnBot(let name):
            if let name { streamingBot = name }
        case .unknown:
            break
        }
    }

    @MainActor
    private func settleStream(reply: String? = nil) {
        let finalText = reply ?? streamingText
        if !finalText.isEmpty {
            messages.append(ChatMessage(
                seq: (messages.last?.seq ?? 0) + 1,
                role: "assistant",
                content: .text(finalText),
                toolCalls: nil, toolCallId: nil, createdAt: nil,
                feedback: nil, pinned: nil,
                bot: streamingBot.map { BotAttribution(id: nil, name: $0, handle: $0) }
            ))
        }
        streamingText = ""
        thinkingText = ""
        toolActivity = nil
        streamingBot = nil
        isStreaming = false
        // Pull authoritative history in the background (titles, attribution).
        if let sid = resolvedSessionId ?? sessionId {
            Task { await refreshTranscript(sid) }
        }
    }

    private func refreshTranscript(_ sid: String) async {
        guard let client = servers.client else { return }
        if let session = try? await client.session(sid) {
            messages = session.messages
            title = session.title
        }
    }

    private func stopStreaming() {
        streamTask?.cancel()
        isStreaming = false
        if let sid = effectiveSessionId, let client = servers.client {
            Task { try? await client.cancelTurn(sessionId: sid) }
        }
        settleStream()
    }

    private func resyncAfterBackground() async {
        guard let client = servers.client, let sid = effectiveSessionId else { return }
        await refreshTranscript(sid)
        // Re-attach to a turn that survived backgrounding.
        if let active = try? await client.activeTurn(sessionId: sid), active, !isStreaming {
            isStreaming = true
            streamTask = Task {
                do {
                    for try await event in client.turnStream(sessionId: sid) {
                        await MainActor.run { handle(event) }
                    }
                    await MainActor.run { settleStream() }
                } catch {
                    await MainActor.run { settleStream() }
                }
            }
        }
    }

    // MARK: - Session events channel (HITL + queue)

    private func startEventsChannel() {
        guard let client = servers.client, let sid = effectiveSessionId else { return }
        eventsTask = Task {
            // Reconnect loop: SSE dies on network hiccups; server keeps state.
            while !Task.isCancelled {
                do {
                    for try await ev in client.sessionEvents(sessionId: sid) {
                        if Task.isCancelled { break }
                        await MainActor.run { handleSessionEvent(ev) }
                    }
                } catch {
                    // Not connected or transient — retry with backoff.
                }
                if Task.isCancelled { break }
                try? await Task.sleep(for: .seconds(3))
            }
        }
    }

    @MainActor
    private func handleSessionEvent(_ ev: SSEEvent) {
        switch ev.event ?? "" {
        case "user_request", "user_request_auto":
            Task {
                if let pending = try? await servers.client?.pendingRequest(sessionId: effectiveSessionId ?? "") {
                    self.pendingRequest = pending
                    seedFormValues(pending)
                }
            }
        case "user_request_cancelled":
            pendingRequest = nil
        case "user_enqueued":
            if case .userEnqueued(let q) = ChatStreamEvent.decode(event: ev.event, data: ev.data),
               let qid = q.qid, let text = q.text, !queuedMessages.contains(where: { $0.id == qid }) {
                queuedMessages.append(QueuedMessage(id: qid, text: text))
            }
        case "user_injected":
            if case .userInjected(let q) = ChatStreamEvent.decode(event: ev.event, data: ev.data), let qid = q.qid {
                queuedMessages.removeAll(where: { $0.id == qid })
            }
        case "queue_removed":
            if case .queueRemoved(let qid) = ChatStreamEvent.decode(event: ev.event, data: ev.data) {
                queuedMessages.removeAll(where: { $0.id == qid })
            }
        case "turn_started":
            // Turn kicked off on another surface (browser, extension) while
            // this screen is open — attach to its stream to follow live.
            if !isStreaming, let client = servers.client, let sid = effectiveSessionId {
                attachTurnStream(client: client, sessionId: sid)
            }
        case "turn_settled":
            // A turn that ran elsewhere finished — settle any attached view
            // and pull the authoritative transcript from the server.
            if isStreaming { settleStream() }
            if let sid = effectiveSessionId {
                Task { await refreshTranscript(sid) }
            }
        default:
            break
        }
    }

    // MARK: - HITL

    private func seedFormValues(_ request: PendingRequest) {
        formValues = [:]
        if let fields = request.form?.fields {
            for field in fields {
                formValues[field.name] = field.choices?.first ?? ""
            }
        }
    }

    private func respond(_ answer: String) {
        guard let sid = effectiveSessionId, let request = pendingRequest else { return }
        pendingRequest = nil
        Task {
            try? await servers.client?.respond(sessionId: sid, requestId: request.requestId, answer: answer)
        }
    }

    private func cancelPrompt() async {
        guard let sid = effectiveSessionId, let request = pendingRequest, let client = servers.client else { return }
        pendingRequest = nil
        var urlReq = URLRequest(url: client.profile.baseURL.appendingPathComponent("chat/\(sid)/hitl/\(request.requestId)/cancel"))
        urlReq.httpMethod = "POST"
        urlReq.setValue("Bearer \(client.profile.token)", forHTTPHeaderField: "Authorization")
        _ = try? await URLSession.shared.data(for: urlReq)
    }
}

// MARK: - Bubbles

struct MessageBubble: View {
    let message: ChatMessage

    private var isUser: Bool { message.role == "user" }

    var body: some View {
        VStack(alignment: isUser ? .trailing : .leading, spacing: 4) {
            if let bot = message.bot {
                Badge(text: "@\(bot.handle ?? bot.name ?? "bot")")
            }
            if message.role == "tool" {
                Label(String(message.content.displayText.prefix(80)), systemImage: "wrench.and.screwdriver")
                    .font(.caption2)
                    .foregroundStyle(Theme.subtleText)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                (isUser ? AnyView(Text(message.content.displayText)) : AnyView(MarkdownText(markdown: message.content.displayText)))
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: isUser ? .trailing : .leading)
                    .background(isUser ? Theme.bubbleUser : Theme.bubbleBot, in: RoundedRectangle(cornerRadius: 14))
            }
        }
    }
}

struct ThinkingBlock: View {
    let text: String
    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "brain")
                .font(.caption)
                .foregroundStyle(Theme.subtleText)
            Text(String(text.prefix(600)))
                .font(.caption.italic())
                .foregroundStyle(Theme.subtleText)
                .lineLimit(6)
        }
        .padding(10)
        .background(Theme.subtleText.opacity(0.06), in: RoundedRectangle(cornerRadius: 10))
    }
}

struct TypingDots: View {
    @State private var phase = false
    var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<3, id: \.self) { i in
                Circle()
                    .frame(width: 6, height: 6)
                    .foregroundStyle(Theme.subtleText)
                    .opacity(phase ? 1 : 0.3)
                    .animation(.easeInOut(duration: 0.6).repeatForever().delay(Double(i) * 0.2), value: phase)
            }
        }
        .onAppear { phase = true }
    }
}

// MARK: - HITL prompt surfaces

/// confirm / choice / text / form — the ask_user kinds.
struct HitlSheet: View {
    let request: PendingRequest
    var onAnswer: (String) -> Void
    var onCancel: () -> Void

    @State private var textAnswer = ""
    @State private var choice: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(request.prompt, systemImage: "question.circle.fill")
                .font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity, alignment: .leading)

            switch request.kind {
            case "confirm":
                HStack {
                    ForEach(["Yes", "No"], id: \.self) { label in
                        Button(label) {
                            onAnswer(label.lowercased() == "yes" ? "yes" : "no")
                        }
                        .buttonStyle(.bordered)
                    }
                    Spacer()
                }
            case "choice":
                FlowChoices(choices: request.choices ?? []) { picked in
                    onAnswer(picked)
                }
            case "form":
                FormFields(request: request) { dict in
                    if let data = try? JSONSerialization.data(withJSONObject: dict),
                       let json = String(data: data, encoding: .utf8) {
                        onAnswer(json)
                    }
                }
            default: // "text" and anything unknown
                TextField("Type your answer…", text: $textAnswer, axis: .vertical)
                    .lineLimit(1...4)
                    .padding(8)
                    .background(Theme.background, in: RoundedRectangle(cornerRadius: 8))
                    .onSubmit { submitText() }
                HStack {
                    Button("Send") { submitText() }
                        .buttonStyle(.borderedProminent)
                        .disabled(textAnswer.isEmpty)
                    Spacer()
                }
            }

            Button("Dismiss", role: .destructive) { onCancel() }
                .font(.caption)
        }
    }

    private func submitText() {
        guard !textAnswer.isEmpty else { return }
        onAnswer(textAnswer)
    }
}

struct FlowChoices: View {
    let choices: [String]
    let onPick: (String) -> Void

    var body: some View {
        FlowLayout(spacing: 6) {
            ForEach(choices, id: \.self) { choice in
                Button(choice) { onPick(choice) }
                    .font(.subheadline)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(Theme.accent.opacity(0.14), in: Capsule())
            }
        }
    }
}

struct FormFields: View {
    let request: PendingRequest
    let onSubmit: ([String: Any]) -> Void

    @State private var values: [String: String] = [:]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let title = request.form?.title {
                Text(title).font(.subheadline.weight(.semibold))
            }
            ForEach(request.form?.fields ?? []) { field in
                VStack(alignment: .leading, spacing: 3) {
                    Text(field.label ?? field.name)
                        .font(.caption.weight(.medium))
                    fieldEditor(field)
                }
            }
            Button("Submit") {
                var out: [String: Any] = [:]
                for field in request.form?.fields ?? [] {
                    let raw = values[field.name] ?? field.choices?.first ?? field.default ?? ""
                    out[field.name] = coerce(raw, field)
                }
                onSubmit(out)
            }
            .buttonStyle(.borderedProminent)
        }
        .onAppear {
            for field in request.form?.fields ?? [] {
                values[field.name] = field.choices?.first ?? field.default ?? ""
            }
        }
    }

    @ViewBuilder
    private func fieldEditor(_ field: FormField) -> some View {
        switch field.kind {
        case "select":
            Picker("", selection: Binding(
                get: { values[field.name] ?? "" },
                set: { values[field.name] = $0 }
            )) {
                ForEach(field.choices ?? [], id: \.self) { Text($0).tag($0) }
            }
            .pickerStyle(.menu)
        case "boolean":
            Toggle("", isOn: Binding(
                get: { (values[field.name] ?? "false") == "true" },
                set: { values[field.name] = $0 ? "true" : "false" }
            ))
            .labelsHidden()
        case "textarea":
            TextField(field.placeholder ?? "", text: Binding(
                get: { values[field.name] ?? "" },
                set: { values[field.name] = $0 }
            ), axis: .vertical)
            .lineLimit(3...6)
        default:
            SecureFieldOrPlain(placeholder: field.placeholder ?? "", text: Binding(
                get: { values[field.name] ?? "" },
                set: { values[field.name] = $0 }
            ), secret: field.secret == true)
            .autocorrectionDisabled()
            .textInputAutocapitalization(.never)
        }
    }

    private func coerce(_ raw: String, _ field: FormField) -> Any {
        switch field.kind {
        case "number": return Double(raw) ?? 0
        case "boolean": return raw == "true"
        default: return raw
        }
    }
}

struct SecureFieldOrPlain: View {
    let placeholder: String
    @Binding var text: String
    let secret: Bool

    var body: some View {
        if secret {
            SecureField(placeholder, text: $text)
        } else {
            TextField(placeholder, text: $text)
        }
    }
}

/// Simple wrapping layout for choice chips.
struct FlowLayout: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 320
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0
        for sub in subviews {
            let size = sub.sizeThatFits(.unspecified)
            if x + size.width > width, x > 0 {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
        return CGSize(width: width, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, rowHeight: CGFloat = 0
        for sub in subviews {
            let size = sub.sizeThatFits(.unspecified)
            if x + size.width > bounds.maxX, x > bounds.minX {
                x = bounds.minX
                y += rowHeight + spacing
                rowHeight = 0
            }
            sub.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}

extension View {
    func hideKeyboard() {
        UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
    }
}
