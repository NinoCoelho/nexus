import SwiftUI
import UIKit
import UniformTypeIdentifiers

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
    /// False until the initial batch populate has been laid out AND the
    /// viewport has landed on the newest message — gates the reveal so the
    /// user never sees the top-of-transcript flash before the jump.
    @State private var initialSettled = false
    @State private var eventsTask: Task<Void, Never>?
    @State private var streamTask: Task<Void, Never>?
    @State private var autoScroll = true
    @State private var lastSentText = ""
    // Attachment staging: files picked here are uploaded to the vault when
    // the turn is sent, and the outgoing message references them by path.
    @State private var stagedAttachments: [StagedAttachment] = []
    @State private var showCamera = false
    @State private var showLibrary = false
    @State private var showFileImporter = false
    @State private var attachmentUploading = false
    /// Composer text view height (grows with content, caps at max lines).
    @State private var composerHeight: CGFloat = 22

    // Scroll: the transcript is a full (non-lazy) VStack; these track
    // whether the viewport sits at the end (drives autoScroll and the
    // jump-to-bottom button) and carry the one-shot scroll after load.
    @State private var isAtBottom = true
    @State private var initialScrollToken = 0

    // In-chat search (local, over the in-memory transcript).
    @State private var showSearch = false
    @State private var searchText = ""
    @State private var searchMatches: [Int] = []   // seqs, oldest → newest
    @State private var searchCursor = 0            // index into searchMatches
    @FocusState private var searchFocused: Bool

    struct QueuedMessage: Identifiable, Equatable {
        let id: String
        var text: String
    }

    private var effectiveSessionId: String? { resolvedSessionId ?? sessionId }

    var body: some View {
        VStack(spacing: 0) {
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
        // Toast: any network/upload error must surface even when history is
        // already on screen — not only on a blank transcript.
        .overlay(alignment: .top) {
            if let error {
                ErrorBanner(message: error) { self.error = nil }
                    .padding(.horizontal, 12)
                    .padding(.top, 4)
                    .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .animation(.easeInOut(duration: 0.25), value: error)
        .navigationTitle(title.isEmpty ? (bot?.name ?? "Chat") : title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    toggleSearch()
                } label: {
                    Image(systemName: "magnifyingglass")
                }
                .accessibilityLabel("Search in chat")
            }
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
            VStack(spacing: 0) {
                if showSearch {
                    searchBar(proxy: proxy)
                }
                GeometryReader { viewport in
                    ZStack {
                        ScrollView {
                            VStack(spacing: 10) {
                                ForEach(displayMessages) { message in
                                    MessageBubble(message: message, highlight: highlightTerm)
                                        .id(message.seq)
                                }
                                if searchNoResults {
                                    Text("No matches")
                                        .font(.caption)
                                        .foregroundStyle(Theme.subtleText)
                                        .padding(.top, 40)
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
                            .background(bottomSentinel)
                        }
                        .overlay(alignment: .bottom) {
                            jumpToBottom(proxy: proxy)
                        }
                        // Hidden (but laid out, so heights are real) until
                        // the initial populate has been scrolled to the
                        // bottom — kills the open-at-top flash.
                        .opacity(initialSettled ? 1 : 0)
                        if !initialSettled {
                            ProgressView()
                        }
                    }
                    .coordinateSpace(name: "chatScroll")
                    .onPreferenceChange(ChatBottomKey.self) { bottomY in
                        // Distance between the transcript's bottom edge and
                        // the viewport bottom — small (≤64pt) means the user
                        // is at the end. Streaming only keeps the view pinned
                        // while this holds; scrolling up releases it.
                        let distance = bottomY - viewport.size.height
                        let atBottom = distance <= 64
                        if atBottom != isAtBottom {
                            isAtBottom = atBottom
                            autoScroll = atBottom
                        }
                    }
                    // The keyboard shrinks the scroll view without moving
                    // the offset — re-land on the bottom if we were there.
                    .onReceive(NotificationCenter.default.publisher(for: UIApplication.keyboardWillShowNotification)) { _ in
                        guard autoScroll, !showSearch else { return }
                        Task { @MainActor in
                            try? await Task.sleep(for: .seconds(0.3))
                            if autoScroll, let last = messages.last {
                                proxy.scrollTo(last.seq, anchor: .bottom)
                            }
                        }
                    }
                    .onChange(of: initialScrollToken) { _, _ in
                        // Explicit landing after the initial batch populate:
                        // every row of the (non-lazy) stack is realized, so
                        // the offset is computed against true heights.
                        if let last = messages.last {
                            proxy.scrollTo(last.seq, anchor: .bottom)
                        }
                    }
                    .onChange(of: messages.count) { _, _ in
                        if showSearch { computeSearchMatches() }
                        guard autoScroll else { return }
                        if isStreaming {
                            withAnimation { proxy.scrollTo("streaming", anchor: .bottom) }
                        } else if let last = messages.last {
                            withAnimation { proxy.scrollTo(last.seq, anchor: .bottom) }
                        }
                    }
                    .onChange(of: streamingText) { _, _ in
                        if autoScroll, isStreaming {
                            proxy.scrollTo("streaming", anchor: .bottom)
                        }
                    }
                    .onTapGesture { hideKeyboard() }
                }
            }
        }
    }

    /// Reports the content's bottom edge position in the scroll coordinate
    /// space (consumed by onPreferenceChange above).
    private var bottomSentinel: some View {
        GeometryReader { geo in
            Color.clear.preference(
                key: ChatBottomKey.self,
                value: geo.frame(in: .named("chatScroll")).maxY
            )
        }
    }

    /// Floating "go to end" button — visible whenever the viewport is away
    /// from the transcript's bottom.
    private func jumpToBottom(proxy: ScrollViewProxy) -> some View {
        Group {
            if !isAtBottom {
                Button {
                    withAnimation(.easeOut(duration: 0.25)) {
                        proxy.scrollTo(messages.last?.seq, anchor: .bottom)
                    }
                } label: {
                    Image(systemName: "chevron.down")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Theme.accent)
                        .padding(10)
                        .background(Theme.panel, in: Circle())
                        .overlay(Circle().stroke(Theme.accent.opacity(0.25), lineWidth: 1))
                        .shadow(color: .black.opacity(0.15), radius: 4, y: 2)
                }
                .accessibilityLabel("Jump to latest message")
                .padding(.bottom, 10)
                .transition(.opacity.combined(with: .scale(scale: 0.8)))
            }
        }
        .animation(.easeInOut(duration: 0.2), value: isAtBottom)
    }

    private struct ChatBottomKey: PreferenceKey {
        static var defaultValue: CGFloat = 0
        static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
            value = nextValue()
        }
    }

    // MARK: - In-chat search

    /// Messages actually rendered: unfiltered when no search is running;
    /// only the search hits (seqs preserved, so scrolling by id still
    /// works); empty when the query matched nothing.
    private var displayMessages: [ChatMessage] {
        guard showSearch, !searchQuery.isEmpty else { return messages }
        guard !searchMatches.isEmpty else { return [] }
        let hits = Set(searchMatches)
        return messages.filter { hits.contains($0.seq) }
    }

    private var searchQuery: String {
        searchText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var searchNoResults: Bool {
        showSearch && !searchQuery.isEmpty && searchMatches.isEmpty
    }

    private var highlightTerm: String? {
        showSearch && !searchQuery.isEmpty ? searchQuery : nil
    }

    private func searchBar(proxy: ScrollViewProxy) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .font(.subheadline)
                .foregroundStyle(Theme.subtleText)
            TextField("Search in chat", text: $searchText)
                .textFieldStyle(.plain)
                .focused($searchFocused)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .submitLabel(.search)
                .onSubmit { stepSearch(+1, proxy: proxy) }
            if !searchQuery.isEmpty {
                Text(searchCounter)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(Theme.subtleText)
                    .fixedSize()
            }
            Button {
                stepSearch(-1, proxy: proxy)
            } label: {
                Image(systemName: "chevron.up")
            }
            .disabled(searchMatches.isEmpty)
            Button {
                stepSearch(+1, proxy: proxy)
            } label: {
                Image(systemName: "chevron.down")
            }
            .disabled(searchMatches.isEmpty)
            Button {
                toggleSearch()
            } label: {
                Image(systemName: "xmark")
            }
            .accessibilityLabel("Close search")
        }
        .font(.subheadline)
        .foregroundStyle(Theme.subtleText)
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Theme.panel)
        .onAppear { searchFocused = true }
        .onChange(of: searchText) { _, _ in
            computeSearchMatches()
            if let newest = searchMatches.last {
                withAnimation { proxy.scrollTo(newest, anchor: .center) }
            }
        }
    }

    private var searchCounter: String {
        searchMatches.isEmpty ? "No matches" : "\(searchCursor + 1)/\(searchMatches.count)"
    }

    private func toggleSearch() {
        withAnimation(.easeInOut(duration: 0.2)) { showSearch.toggle() }
        if showSearch {
            searchFocused = true
        } else {
            searchFocused = false
            clearSearch()
        }
    }

    /// Local filter over the in-memory transcript — case- and
    /// diacritic-insensitive. Cursor lands on the newest match.
    private func computeSearchMatches() {
        guard !searchQuery.isEmpty else {
            searchMatches = []
            searchCursor = 0
            return
        }
        searchMatches = messages
            .filter {
                $0.content.displayText.range(
                    of: searchQuery,
                    options: [.caseInsensitive, .diacriticInsensitive]
                ) != nil
            }
            .map(\.seq)
        searchCursor = searchMatches.isEmpty ? 0 : searchMatches.count - 1
    }

    private func stepSearch(_ direction: Int, proxy: ScrollViewProxy) {
        guard !searchMatches.isEmpty else { return }
        searchCursor = (searchCursor + direction + searchMatches.count) % searchMatches.count
        withAnimation { proxy.scrollTo(searchMatches[searchCursor], anchor: .center) }
    }

    private func clearSearch() {
        searchText = ""
        searchMatches = []
        searchCursor = 0
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
        VStack(spacing: 6) {
            if !stagedAttachments.isEmpty {
                stagedStrip
            }
            HStack(alignment: .bottom, spacing: 8) {
                Menu {
                    Button {
                        showCamera = true
                    } label: {
                        Label("Take photo", systemImage: "camera")
                    }
                    Button {
                        showLibrary = true
                    } label: {
                        Label("Photo library", systemImage: "photo")
                    }
                    Button {
                        pasteFromClipboard(menu: true)
                    } label: {
                        Label("Paste", systemImage: "doc.on.clipboard")
                    }
                    Button {
                        showFileImporter = true
                    } label: {
                        Label("Files…", systemImage: "folder")
                    }
                } label: {
                    Image(systemName: "paperclip")
                        .font(.system(size: 20))
                        .foregroundStyle(Theme.subtleText)
                }
                .disabled(pendingRequest != nil || attachmentUploading)
                .accessibilityLabel("Attach")

                ComposerTextView(
                    text: $input,
                    placeholder: isStreaming ? "Queue a message…" : "Message",
                    disabled: pendingRequest != nil,
                    onPasteAttachment: { pasteFromClipboard(menu: false) },
                    onSubmit: { send() },
                    onHeightChange: { composerHeight = $0 }
                )
                .frame(height: composerHeight)
                .padding(10)
                .background(Theme.panel, in: RoundedRectangle(cornerRadius: 18))
                Button {
                    send()
                } label: {
                    Image(systemName: isStreaming ? "plus.circle" : "arrow.up.circle.fill")
                        .font(.system(size: 30))
                        .foregroundStyle(composerHasContent ? Theme.accent : Theme.subtleText)
                }
                .disabled(!composerHasContent)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(.bar)
        .sheet(isPresented: $showCamera) {
            CameraPicker { data in
                stage(name: nil, data: data)
            } onCancel: {}
            .ignoresSafeArea()
        }
        .sheet(isPresented: $showLibrary) {
            PhotoLibraryPicker { medias in
                for media in medias {
                    stage(name: media.name, data: media.data)
                }
            } onOversized: { name in
                error = "\"\(name)\" is too large to attach (limit ~300 MB)."
            } onCancel: {}
            .ignoresSafeArea()
        }
        .fileImporter(
            isPresented: $showFileImporter,
            allowedContentTypes: [.item],
            allowsMultipleSelection: true
        ) { result in
            handleFileSelection(result)
        }
    }

    private var composerHasContent: Bool {
        !input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !stagedAttachments.isEmpty
    }

    /// Chips of files waiting to upload — thumbnail for images, icon for
    /// documents. Remove button un-stages the file.
    private var stagedStrip: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                if attachmentUploading {
                    ProgressView()
                    Text("Uploading…").font(.caption).foregroundStyle(Theme.subtleText)
                }
                ForEach(stagedAttachments) { staged in
                    HStack(spacing: 4) {
                        if let thumb = staged.thumbnail {
                            Image(uiImage: thumb)
                                .resizable()
                                .aspectRatio(contentMode: .fill)
                                .frame(width: 36, height: 36)
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                        } else {
                            Image(systemName: staged.icon)
                                .font(.caption)
                        }
                        Text(staged.name)
                            .font(.caption)
                            .lineLimit(1)
                            .frame(maxWidth: 90)
                        Button {
                            stagedAttachments.removeAll { $0.id == staged.id }
                        } label: {
                            Image(systemName: "xmark")
                                .font(.caption2)
                                .foregroundStyle(Theme.subtleText)
                        }
                        .buttonStyle(.plain)
                    }
                    .padding(6)
                    .background(Theme.accent.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
                }
            }
            .padding(.horizontal, 12)
        }
    }

    // MARK: - Data flow

    private func initialLoad() async {
        guard let client = servers.client, let sid = effectiveSessionId else {
            initialSettled = true
            return
        }
        do {
            let session = try await client.session(sid)
            messages = session.messages
            title = session.title
            error = nil
            // The transcript is a static (non-lazy) VStack holding the whole
            // history: give it one layout pass so every row is realized,
            // then land the viewport explicitly on the newest message —
            // revealing the content only after the jump (no top-flash). The
            // old .defaultScrollAnchor(.bottom) estimated heights for rows
            // that hadn't been realized yet and opened on a blank viewport.
            try? await Task.sleep(for: .seconds(0.12))
            initialScrollToken += 1
            initialSettled = true
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
            initialSettled = true
        }
    }

    // MARK: - Attachment staging

    private func stage(name: String?, data: Data) {
        let staged = Staging.attachment(name: name ?? Staging.photoName(), data: data)
        stagedAttachments.append(staged)
    }
    private func pasteFromClipboard(menu: Bool) {
        let pasteboard = UIPasteboard.general
        var stagedAttachment = false
        // Screenshot / copied image → attachment. A mixed clipboard (image +
        // text) stages the image *and* keeps its text for the draft below.
        if let image = pasteboard.image, let data = image.jpegData(compressionQuality: 0.85) {
            stage(name: nil, data: data)
            stagedAttachment = true
        }
        // Videos / files copied in the Files app land as item providers.
        let providers = pasteboard.itemProviders.filter {
            $0.hasItemConformingToTypeIdentifier(UTType.movie.identifier) ||
            $0.hasItemConformingToTypeIdentifier(UTType.image.identifier)
        }
        if !providers.isEmpty {
            stagedAttachment = true
            Task {
                let medias = await PasteboardLoader.load(providers)
                await MainActor.run {
                    for media in medias {
                        stage(name: media.name, data: media.data)
                    }
                    if medias.count < providers.count {
                        error = "Some clipboard items couldn't be read."
                    }
                }
            }
        }
        // Text: the menu always inserts it; a paste gesture only reaches
        // here when the clipboard also carried an attachment (which consumed
        // the stock insertion path), so the text still goes into the draft.
        if let text = pasteboard.string, !text.isEmpty, menu || stagedAttachment {
            input += (input.isEmpty ? "" : " ") + text
            return
        }
        if stagedAttachment { return }
        if menu {
            error = "The clipboard is empty."
        }
    }

    private func handleFileSelection(_ result: Result<[URL], Error>) {
        guard case .success(let urls) = result else { return }
        for url in urls {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            if let data = try? Data(contentsOf: url) {
                stage(name: url.lastPathComponent, data: data)
            }
        }
    }

    // MARK: - Send

    private func send() {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let client = servers.client, !isStreaming else { return }
        guard !text.isEmpty || !stagedAttachments.isEmpty else { return }
        let staged = stagedAttachments
        input = ""
        stagedAttachments = []

        // Optimistic user bubble (new chat: becomes seq 0; server truth
        // replaces it on done). With attachments, show a parts bubble —
        // vault paths are filled in server-side; 📎 names here are display
        // only.
        let optimisticSeq = (messages.last?.seq ?? 0) + 1
        let optimisticContent: MessageContent
        if staged.isEmpty {
            optimisticContent = .text(text)
        } else {
            var parts: [ContentPart] = text.isEmpty
                ? []
                : [ContentPart(kind: "text", vaultPath: nil, text: text)]
            parts += staged.map { s in
                ContentPart(kind: Staging.contentKind(mime: s.mimeType), vaultPath: s.name, text: nil)
            }
            optimisticContent = .parts(parts)
        }
        let optimistic = ChatMessage(
            seq: optimisticSeq,
            role: "user",
            content: optimisticContent,
            toolCalls: nil, toolCallId: nil, createdAt: nil,
            feedback: nil, pinned: nil, bot: nil
        )
        messages.append(optimistic)

        guard !staged.isEmpty else {
            startStream(client: client, message: text, sessionId: effectiveSessionId)
            return
        }
        attachmentUploading = true
        Task {
            var attachments: [ChatAttachment] = []
            var failure: String?
            for s in staged {
                do {
                    let path = try await client.uploadFile(data: s.data, filename: s.name, mimeType: s.mimeType)
                    attachments.append(ChatAttachment(vaultPath: path, mimeType: s.mimeType))
                } catch {
                    failure = error.localizedDescription
                    break
                }
            }
            await MainActor.run {
                attachmentUploading = false
                if let failure {
                    // Nothing leaked to the server — restore the draft.
                    self.error = failure
                    messages.removeAll { $0.seq == optimisticSeq }
                    input = text
                    stagedAttachments = staged
                    return
                }
                startStream(
                    client: client,
                    message: text,
                    attachments: attachments,
                    sessionId: effectiveSessionId
                )
            }
        }
    }

    private func startStream(
        client: NexusClient,
        message: String,
        attachments: [ChatAttachment]? = nil,
        sessionId: String?
    ) {
        isStreaming = true
        streamingText = ""
        thinkingText = ""
        toolActivity = nil
        streamingBot = nil
        lastSentText = message
        streamTask = Task {
            do {
                for try await event in client.chatStream(
                    sessionId: sessionId, message: message, attachments: attachments, projectId: bot.map(\.id)
                ) {
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
        try? await client.hitlCancel(sessionId: sid, requestId: request.requestId)
    }
}

// MARK: - Bubbles

struct MessageBubble: View {
    let message: ChatMessage
    /// Search term to highlight inside the bubble, if a search is active.
    var highlight: String? = nil

    private var isUser: Bool { message.role == "user" }

    /// User bubbles are plain text; highlight by wrapping in an
    /// AttributedString when a search term is active.
    private var userText: Text {
        let raw = message.content.displayText
        guard let highlight else { return Text(raw) }
        return Text(MarkdownText.applyingHighlight(highlight, to: AttributedString(raw)))
    }

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
                (isUser
                    ? AnyView(userText)
                    : AnyView(MarkdownText(markdown: message.content.displayText, highlight: highlight)))
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: isUser ? .trailing : .leading)
                    .background(isUser ? Theme.bubbleUser : Theme.bubbleBot, in: RoundedRectangle(cornerRadius: 14))
            }
        }
        .contextMenu {
            Button {
                UIPasteboard.general.string = message.content.displayText
                UINotificationFeedbackGenerator().notificationOccurred(.success)
            } label: {
                Label("Copy", systemImage: "doc.on.doc")
            }
            // Future message actions (Speak, 👍/👎 feedback) plug in here.
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
