import SwiftUI

/// Per-bot browser live view: CDP screencast over SSE + touch-driven
/// control through POST /browser/action (tap/scroll/type/key/navigate).
struct BrowserView: View {
    @Environment(ServerStore.self) private var servers

    @State private var instances: [BrowserInstanceStatus] = []
    @State private var bots: [Bot] = []
    @State private var error: String?
    @State private var loading = true

    var body: some View {
        NavigationStack {
            List {
                if let error {
                    Section { ErrorBanner(message: error) }
                }
                if instances.isEmpty && !loading && error == nil {
                    Section {
                        Label("No browsers running — open one from a bot", systemImage: "info.circle")
                            .foregroundStyle(Theme.subtleText)
                    }
                }
                Section {
                    ForEach(instances) { instance in
                        NavigationLink {
                            BrowserLiveView(key: instance.key, botName: name(for: instance.key))
                        } label: {
                            HStack {
                                Image(systemName: instance.running ? "safari.fill" : "safari")
                                    .foregroundStyle(instance.running ? Theme.accent : Theme.subtleText)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(name(for: instance.key))
                                        .fontWeight(.semibold)
                                    if let url = instance.url {
                                        Text(url)
                                            .font(.caption)
                                            .foregroundStyle(Theme.subtleText)
                                            .lineLimit(1)
                                    }
                                }
                                Spacer()
                                Circle()
                                    .fill(instance.running ? Color.green : Color.gray.opacity(0.5))
                                    .frame(width: 8, height: 8)
                            }
                        }
                    }
                } header: {
                    Text("Browsers")
                } footer: {
                    if servers.activeStatus?.features?.browserRemoteControl != true {
                        Text("Remote control is off on this server. Enable with [browser] remote_control = true in config.toml to drive browsers from here.")
                    }
                }
            }
            .navigationTitle("Browser")
            .refreshable { await load() }
            .overlay { if loading && instances.isEmpty { ProgressView() } }
            .task { await load() }
        }
    }

    private func name(for key: String) -> String {
        if key == "main" {
            return bots.first(where: { $0.isCoordinator })?.name ?? "Main"
        }
        if key.hasPrefix("project:") {
            let id = String(key.dropFirst("project:".count))
            return bots.first(where: { $0.id == id })?.name ?? key
        }
        return key
    }

    private func load() async {
        guard let client = servers.client else { return }
        loading = false
        do {
            async let i = client.browserInstances()
            async let b = client.bots()
            instances = try await i
            bots = try await b
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: - Live view + control

struct BrowserLiveView: View {
    let key: String
    let botName: String

    @Environment(ServerStore.self) private var servers
    @Environment(\.displayScale) private var displayScale

    @State private var frames: [UIImage] = []
    @State private var infoUrl: String?
    @State private var infoTitle: String?
    @State private var connected = false
    @State private var statusNote: String?
    @State private var controlBlocked = false
    @State private var zoom: CGFloat = 1
    @State private var keyboardText = ""
    @State private var urlField = ""
    @State private var streamTask: Task<Void, Never>?
    @State private var lastDragDelta: CGFloat = 0

    private var latestFrame: UIImage? { frames.last }

    var body: some View {
        VStack(spacing: 0) {
            infoBar
            GeometryReader { geo in
                ZStack {
                    Color.black.ignoresSafeArea()
                    if let image = latestFrame {
                        imageView(image, container: geo.size)
                    } else {
                        VStack(spacing: 10) {
                            ProgressView()
                            Text(statusNote ?? "Connecting…")
                                .font(.caption)
                                .foregroundStyle(.white.opacity(0.7))
                        }
                        .foregroundStyle(.white)
                    }
                    if controlBlocked {
                        blockedOverlay
                    }
                }
                .clipped()
            }
            keyboardBar
        }
        .navigationTitle(botName)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button(role: .destructive) {
                    Task {
                        streamTask?.cancel()
                        try? await servers.client?.browserStop(key: key)
                    }
                } label: {
                    Image(systemName: "stop.circle")
                }
            }
        }
        .task { startStream() }
        .onDisappear { streamTask?.cancel() }
    }

    // MARK: Frame + gestures

    @State private var renderedFrameSize: CGSize = .zero

    private func imageView(_ image: UIImage, container: CGSize) -> some View {
        let fitted = fitSize(image.size, in: container)
        return Image(uiImage: image)
            .resizable()
            .aspectRatio(contentMode: .fit)
            .frame(width: fitted.width, height: fitted.height)
            .scaleEffect(zoom)
            .background(FrameSizeReader { size in
                renderedFrameSize = size
            })
            .gesture(
                DragGesture(minimumDistance: 8)
                    .onChanged { value in
                        lastDragDelta = value.translation.height
                    }
                    .onEnded { value in
                        // Finger up = natural scroll direction on a phone.
                        let delta = -value.translation.height * 2
                        guard abs(delta) > 12 else { return }
                        sendScroll(delta: delta)
                    }
            )
            .simultaneousGesture(
                MagnifyGesture()
                    .onChanged { value in zoom = max(1, min(4, value.magnification)) }
                    .onEnded { _ in withAnimation(.spring(duration: 0.3)) { zoom = 1 } }
            )
            .onTapGesture(coordinateSpace: .local) { location in
                sendTap(at: location, frameSize: renderedFrameSize == .zero ? fitted : renderedFrameSize)
            }
    }

    private func fitSize(_ source: CGSize, in container: CGSize) -> CGSize {
        guard source.width > 0, source.height > 0 else { return container }
        let scale = min(container.width / source.width, container.height / source.height, 1)
        return CGSize(width: source.width * scale, height: source.height * scale)
    }

    // MARK: Bars

    private var infoBar: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(connected ? Color.green : Color.orange)
                .frame(width: 8, height: 8)
            TextField("URL", text: $urlField)
                .font(.caption.monospaced())
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .onSubmit { navigate(urlField) }
            Button {
                navigate(urlField)
            } label: {
                Image(systemName: "arrow.forward.circle.fill")
            }
            .disabled(urlField.isEmpty)
        }
        .padding(.horizontal)
        .padding(.vertical, 6)
        .background(.bar)
    }

    private var keyboardBar: some View {
        HStack(spacing: 8) {
            TextField("Type into page…", text: $keyboardText)
                .textFieldStyle(.roundedBorder)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .onSubmit { typeText(keyboardText); keyboardText = "" }
            Button {
                typeText(keyboardText + "\n")
                keyboardText = ""
            } label: {
                Image(systemName: "return")
            }
            .disabled(keyboardText.isEmpty)
            Button {
                sendKey("backspace")
            } label: {
                Image(systemName: "delete.left")
            }
            Button {
                sendKey("arrowdown")
            } label: {
                Image(systemName: "chevron.down")
            }
            Button {
                sendKey("arrowup")
            } label: {
                Image(systemName: "chevron.up")
            }
        }
        .padding(8)
        .background(.bar)
    }

    private var blockedOverlay: some View {
        VStack(spacing: 8) {
            Image(systemName: "hand.raised.fill")
            Text("Remote control disabled\non this server")
                .font(.caption)
                .multilineTextAlignment(.center)
        }
        .foregroundStyle(.white)
        .padding(14)
        .background(.black.opacity(0.72), in: RoundedRectangle(cornerRadius: 12))
    }

    // MARK: Stream

    private func startStream() {
        guard let client = servers.client else { return }
        streamTask?.cancel()
        statusNote = "Starting browser…"
        streamTask = Task {
            // Reconnect loop — the stream ends when Chrome dies/restarts.
            while !Task.isCancelled {
                do {
                    for try await frame in client.browserStream(key: key) {
                        if Task.isCancelled { break }
                        await MainActor.run { apply(frame) }
                    }
                } catch let error as APIError {
                    await MainActor.run {
                        statusNote = error.errorDescription
                        if case .forbidden = error { controlBlocked = true }
                    }
                } catch {
                    if Task.isCancelled { break }
                    await MainActor.run { statusNote = "reconnecting…" }
                }
                guard !Task.isCancelled else { break }
                try? await Task.sleep(for: .seconds(2))
                await MainActor.run { connected = false }
            }
        }
    }

    @MainActor
    private func apply(_ frame: BrowserStreamFrame) {
        switch frame {
        case .frame(let jpeg):
            if let image = UIImage(data: jpeg) {
                // Ring buffer — keep the last few frames for smoothness.
                frames.append(image)
                if frames.count > 3 { frames.removeFirst(frames.count - 3) }
                if !connected {
                    connected = true
                    statusNote = nil
                }
            }
        case .info(let url, let title, _, _):
            infoUrl = url
            infoTitle = title
            if let url { urlField = url }
        case .heartbeat:
            connected = true
        case .error(let message):
            statusNote = message
            connected = false
        }
    }

    // MARK: Actions

    private func sendTap(at point: CGPoint, frameSize: CGSize) {
        guard frameSize.width > 0, frameSize.height > 0 else { return }
        let xNorm = Double(point.x / frameSize.width)
        let yNorm = Double(point.y / frameSize.height)
        run { try await $0.browserAction(key: key, action: "tap", xNorm: xNorm, yNorm: yNorm) }
    }

    private func sendScroll(delta: Double) {
        run { try await $0.browserAction(key: key, action: "scroll", deltaY: delta) }
    }

    private func typeText(_ text: String) {
        guard !text.isEmpty else { return }
        run { try await $0.browserAction(key: key, action: "type", text: text) }
    }

    private func sendKey(_ name: String) {
        run { try await $0.browserAction(key: key, action: "key", keyName: name) }
    }

    private func navigate(_ url: String) {
        let target = url.trimmingCharacters(in: .whitespaces)
        guard !target.isEmpty else { return }
        run { try await $0.browserAction(key: key, action: "navigate", url: target) }
    }

    private func run(_ op: @escaping (NexusClient) async throws -> BrowserActionResult) {
        guard let client = servers.client else { return }
        Task {
            do {
                let result = try await op(client)
                if !result.ok, let error = result.error {
                    await MainActor.run { statusNote = error }
                }
            } catch let error as APIError {
                await MainActor.run {
                    if case .forbidden(let detail) = error {
                        controlBlocked = true
                        statusNote = detail
                    } else {
                        statusNote = error.errorDescription
                    }
                }
            } catch {
                await MainActor.run { statusNote = error.localizedDescription }
            }
        }
    }
}

struct FrameSizeReader: View {
    let onChange: (CGSize) -> Void

    var body: some View {
        GeometryReader { geo in
            Color.clear
                .onAppear { onChange(geo.size) }
                .onChange(of: geo.size) { _, newSize in onChange(newSize) }
        }
        .allowsHitTesting(false)
    }
}
