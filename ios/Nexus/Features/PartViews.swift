import SwiftUI
import AVFoundation
import CryptoKit
import UniformTypeIdentifiers
import UIKit

// Rich rendering of non-text content parts inside chat bubbles
// (Phase 2 of the iOS UX plan). Files are fetched from the server with
// the same authenticated client the rest of the app uses
// (GET /vault/raw?path=…) and cached on disk keyed by the vault path.

// MARK: - Content-part classification

extension ContentPart {
    /// Render class for this part. Server kinds map straight through
    /// ("text" / "image" / "audio" / "document"); an unknown kind is
    /// re-sniffed from the path extension so generically stored files
    /// still render richly. Anything unclassifiable keeps the raw kind —
    /// the bubble then renders it as the "📎 <path>" fallback.
    var renderKind: String {
        switch kind {
        case "text", "image", "audio", "document":
            return kind
        default:
            guard let path = vaultPath else { return kind }
            let ext = (path as NSString).pathExtension.lowercased()
            guard !ext.isEmpty, let type = UTType(filenameExtension: ext) else { return kind }
            if type.conforms(to: .image) { return "image" }
            if type.conforms(to: .audio) { return "audio" }
            return kind
        }
    }

    /// Last path component of the vault path (or the kind when absent).
    var fileName: String {
        guard let path = vaultPath, !path.isEmpty else { return kind }
        return (path as NSString).lastPathComponent
    }
}

// MARK: - Vault file cache

/// Disk cache for vault files pulled into bubbles — images to decode,
/// audio to play, documents to share. Keys are the SHA-256 of the vault
/// path; the original extension is preserved on the cached filename so
/// AVAudioPlayer and the share sheet see something sensible.
actor VaultFileCache {
    static let shared = VaultFileCache()

    private let dir: URL
    /// Two bubbles referencing the same vault path (or a transcript
    /// refresh racing a load) must hit the wire once.
    private var inFlight: [String: Task<URL, Error>] = [:]

    private init() {
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        dir = caches.appendingPathComponent("vault-files", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    /// The cached file for a vault path, if present — no network.
    func cachedURL(vaultPath: String) -> URL? {
        let url = Self.keyedURL(dir: dir, vaultPath: vaultPath)
        return (try? url.checkResourceIsReachable()) == true ? url : nil
    }

    /// Resolve a vault path to a local file URL: cache hit, or an
    /// authenticated GET /vault/raw download written into the cache.
    func url(for vaultPath: String, client: NexusClient) async throws -> URL {
        if let cached = cachedURL(vaultPath: vaultPath) { return cached }
        let key = Self.key(vaultPath)
        if let running = inFlight[key] {
            inFlight[key] = nil
            return try await running.value
        }
        let task = Task<URL, Error> { [client] in
            let data = try await client.vaultRawData(path: vaultPath)
            let url = Self.keyedURL(dir: dir, vaultPath: vaultPath)
            try data.write(to: url, options: .atomic)
            return url
        }
        inFlight[key] = task
        do {
            let url = try await task.value
            inFlight[key] = nil
            return url
        } catch {
            inFlight[key] = nil
            throw error
        }
    }

    private static func key(_ vaultPath: String) -> String {
        SHA256.hash(data: Data(vaultPath.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    private static func keyedURL(dir: URL, vaultPath: String) -> URL {
        let ext = (vaultPath as NSString).pathExtension
        let safe = (ext.count <= 8 && ext.allSatisfy { $0.isLetter || $0.isNumber }) ? ext : ""
        return dir.appendingPathComponent(key(vaultPath) + "." + (safe.isEmpty ? "bin" : safe))
    }

    /// Write staged-but-not-yet-uploaded bytes into the cache under a
    /// UUID name so audio can play / documents can share without a
    /// fetch. Returns nil on failure.
    nonisolated static func localFileURL(name: String, data: Data) -> URL? {
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        let dir = caches.appendingPathComponent("vault-files", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let ext = (name as NSString).pathExtension
        let safe = (ext.count <= 8 && ext.allSatisfy { $0.isLetter || $0.isNumber }) ? ext : ""
        let url = dir.appendingPathComponent("local-\(UUID().uuidString)." + (safe.isEmpty ? "bin" : safe))
        do {
            try data.write(to: url, options: .atomic)
            return url
        } catch {
            return nil
        }
    }
}

// MARK: - Shared error chip

/// Failure state for a rich part: error icon, file name, retry button.
struct PartErrorChip: View {
    var label: String
    var onRetry: () -> Void

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.yellow)
            Text(label)
                .lineLimit(1)
                .truncationMode(.middle)
            Spacer(minLength: 4)
            Button {
                onRetry()
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .accessibilityLabel("Retry download")
        }
        .font(.caption)
        .foregroundStyle(.primary)
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(Theme.panel, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(.red.opacity(0.4), lineWidth: 1))
    }
}

// MARK: - Image part

/// Inline image in a bubble: authenticated fetch → disk cache →
/// downsampled UIImage. Tap opens the fullscreen viewer.
struct ImagePartView: View {
    let part: ContentPart
    let client: NexusClient?
    /// Bytes staged locally before the upload reached the vault — lets
    /// the optimistic bubble show the image with no fetch at all.
    var localData: Data? = nil

    @State private var thumb: UIImage?
    @State private var cachedURL: URL?
    @State private var failed = false
    @State private var showViewer = false
    /// Retry token — bumped to re-run the load task.
    @State private var attempt = 0

    private static let box = CGSize(width: 220, height: 165)
    /// Decode budget: ~2.5× the on-screen box so retina rendering of a
    /// 12 MP vault photo costs a thumbnail, not a full bitmap.
    private static let thumbEdge: CGFloat = 660

    var body: some View {
        Group {
            if let thumb {
                Image(uiImage: thumb)
                    .resizable()
                    .scaledToFill()
                    .frame(width: Self.box.width, height: Self.box.height)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .contentShape(RoundedRectangle(cornerRadius: 12))
                    .onTapGesture { showViewer = true }
                    .accessibilityLabel("Attached image, tap to view")
            } else if failed {
                PartErrorChip(label: part.fileName, onRetry: { attempt += 1 })
                    .frame(width: Self.box.width, alignment: .leading)
            } else {
                RoundedRectangle(cornerRadius: 12)
                    .fill(Theme.subtleText.opacity(0.08))
                    .frame(width: Self.box.width, height: Self.box.height)
                    .overlay(ProgressView())
            }
        }
        .task(id: "\(part.vaultPath ?? "")#\(attempt)") { await load() }
        .fullScreenCover(isPresented: $showViewer) {
            ImageViewer(
                thumb: thumb,
                cachedURL: cachedURL,
                localData: cachedURL == nil ? localData : nil,
                title: part.fileName
            )
        }
    }

    private func load() async {
        failed = false
        if let localData {
            thumb = await Self.decode(url: nil, or: localData)
            failed = (thumb == nil)
            return
        }
        guard let client, let path = part.vaultPath, !path.isEmpty else {
            failed = true
            return
        }
        do {
            let url = try await VaultFileCache.shared.url(for: path, client: client)
            cachedURL = url
            thumb = await Self.decode(url: url, or: nil)
            failed = (thumb == nil)
        } catch {
            failed = true
        }
    }

    /// Decode off the main thread, downsampled to the thumbnail budget.
    private static func decode(url: URL?, or data: Data?) async -> UIImage? {
        await Task.detached(priority: .userInitiated) { () -> UIImage? in
            let raw = url.flatMap { try? Data(contentsOf: $0) } ?? data
            guard let raw else { return nil }
            return ImageOps.chipThumbnail(raw, maxEdge: thumbEdge)
        }.value
    }
}

/// Fullscreen image viewer: pinch to zoom (1×–8×), tap anywhere (or ✕)
/// to close. Deliberately cheap — no shared-element transition, no pan
/// bounds; the full-res image loads in the background behind the thumb.
struct ImageViewer: View {
    let thumb: UIImage?
    var cachedURL: URL? = nil
    var localData: Data? = nil
    let title: String

    @Environment(\.dismiss) private var dismiss
    @State private var full: UIImage?
    @State private var scale: CGFloat = 1

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if let full {
                Image(uiImage: full)
                    .resizable()
                    .scaledToFit()
                    .scaleEffect(scale)
                    .gesture(
                        MagnificationGesture()
                            .onChanged { scale = min(max($0, 1), 8) }
                            .onEnded { value in
                                withAnimation(.easeOut(duration: 0.2)) {
                                    scale = min(max(value, 1), 8)
                                }
                            }
                    )
                    .onTapGesture { dismiss() }
            } else {
                VStack(spacing: 12) {
                    if let thumb {
                        Image(uiImage: thumb)
                            .resizable()
                            .scaledToFit()
                            .opacity(0.6)
                    }
                    ProgressView().tint(.white)
                }
                .onTapGesture { dismiss() }
            }
            VStack {
                HStack {
                    Text(title)
                        .font(.footnote)
                        .foregroundStyle(.white.opacity(0.8))
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                    Button {
                        dismiss()
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.title2)
                            .foregroundStyle(.white.opacity(0.8))
                    }
                    .accessibilityLabel("Close")
                }
                .padding(14)
                Spacer()
            }
        }
        .task {
            full = await Self.loadFull(url: cachedURL, or: localData) ?? thumb
        }
    }

    private static func loadFull(url: URL?, or data: Data?) async -> UIImage? {
        await Task.detached(priority: .userInitiated) { () -> UIImage? in
            let raw = url.flatMap { try? Data(contentsOf: $0) } ?? data
            guard let raw else { return nil }
            return UIImage(data: raw)
        }.value
    }
}

// MARK: - Audio part

/// Compact player inside a bubble: play/pause, progress, duration — no
/// autoplay. The file is downloaded with auth into the shared disk
/// cache and played locally with AVAudioPlayer (the server exposes no
/// range-streaming endpoint to lean on).
struct AudioPartView: View {
    let part: ContentPart
    let client: NexusClient?
    var localData: Data? = nil

    @StateObject private var player = AudioPlayerModel()
    @State private var fileURL: URL?
    @State private var failed = false
    @State private var attempt = 0

    var body: some View {
        Group {
            if failed {
                PartErrorChip(label: part.fileName, onRetry: { attempt += 1 })
            } else if let fileURL {
                HStack(spacing: 10) {
                    Button {
                        player.toggle()
                    } label: {
                        Image(systemName: player.playing ? "pause.circle.fill" : "play.circle.fill")
                            .font(.system(size: 30))
                            .foregroundStyle(Theme.accent)
                    }
                    .disabled(!player.loaded)
                    .accessibilityLabel(player.playing ? "Pause" : "Play")
                    VStack(alignment: .leading, spacing: 5) {
                        HStack(spacing: 4) {
                            Image(systemName: "waveform")
                                .font(.caption)
                                .foregroundStyle(Theme.subtleText)
                            Text(part.fileName)
                                .font(.caption)
                                .foregroundStyle(Theme.subtleText)
                                .lineLimit(1)
                                .truncationMode(.middle)
                        }
                        ProgressView(value: player.progress)
                            .tint(Theme.accent)
                        Text(player.timeLabel)
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(Theme.subtleText)
                    }
                }
                .padding(10)
                .frame(minWidth: 210, alignment: .leading)
                .background(Theme.panel, in: RoundedRectangle(cornerRadius: 12))
            } else {
                // Downloading the audio file — same cache as images.
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Label(part.fileName, systemImage: "waveform")
                        .font(.caption)
                        .foregroundStyle(Theme.subtleText)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                .padding(10)
                .frame(minWidth: 210, alignment: .leading)
                .background(Theme.panel, in: RoundedRectangle(cornerRadius: 12))
            }
        }
        .task(id: "\(part.vaultPath ?? "")#\(attempt)") { await load() }
    }

    private func load() async {
        failed = false
        fileURL = nil
        // Staged locally (optimistic bubble): materialize the bytes as a
        // file so AVAudioPlayer can open them.
        if let localData {
            let url = await Task.detached(priority: .utility) {
                VaultFileCache.localFileURL(name: part.fileName, data: localData)
            }.value
            guard let url else {
                failed = true
                return
            }
            fileURL = url
            player.load(url: url)
            return
        }
        guard let client, let path = part.vaultPath, !path.isEmpty else {
            failed = true
            return
        }
        do {
            let url = try await VaultFileCache.shared.url(for: path, client: client)
            fileURL = url
            player.load(url: url)
        } catch {
            failed = true
        }
    }
}

/// AVAudioPlayer wrapper for one audio bubble. All published state is
/// main-actor; the delegate hops back here when playback ends.
@MainActor
final class AudioPlayerModel: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published private(set) var playing = false
    @Published private(set) var progress: Double = 0
    @Published private(set) var duration: Double = 0
    /// False when the file couldn't be opened (unsupported codec) — the
    /// bubble swaps the player for a retry chip.
    @Published private(set) var loaded = false

    private var player: AVAudioPlayer?
    private var timer: Timer?

    var timeLabel: String {
        "\(Self.clock(player?.currentTime ?? 0)) / \(Self.clock(duration))"
    }

    func load(url: URL) {
        stop()
        let p = try? AVAudioPlayer(contentsOf: url)
        p?.delegate = self
        p?.prepareToPlay()
        player = p
        loaded = (p != nil)
        duration = p?.duration ?? 0
        progress = 0
    }

    func toggle() {
        guard let player else { return }
        if player.isPlaying {
            player.pause()
            playing = false
            stopTimer()
        } else {
            player.play()
            playing = true
            startTimer()
        }
    }

    func stop() {
        player?.stop()
        player = nil
        playing = false
        progress = 0
        stopTimer()
    }

    private func startTimer() {
        stopTimer()
        timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
    }

    private func stopTimer() {
        timer?.invalidate()
        timer = nil
    }

    private func tick() {
        guard let player, duration > 0 else { return }
        progress = min(1, player.currentTime / duration)
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            self.playing = false
            self.stopTimer()
            if flag { self.progress = 1 }
        }
    }

    private static func clock(_ t: Double) -> String {
        guard t.isFinite, t >= 0 else { return "0:00" }
        let s = Int(t.rounded())
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}

// MARK: - Document part

/// Document chip: icon by extension, file name, best-effort size (HEAD
/// /vault/raw). Tap downloads with auth into the cache, then a share
/// sheet offers the local file.
struct DocumentPartView: View {
    let part: ContentPart
    let client: NexusClient?
    var localData: Data? = nil

    @State private var size: Int?
    @State private var localURL: URL?
    @State private var downloading = false
    @State private var failed = false
    @State private var showShare = false
    @State private var attempt = 0

    var body: some View {
        Group {
            if failed {
                PartErrorChip(label: part.fileName, onRetry: { attempt += 1 })
            } else {
                Button {
                    downloadAndShare()
                } label: {
                    HStack(spacing: 8) {
                        if downloading {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: Self.icon(for: part.fileName))
                                .font(.system(size: 18))
                                .foregroundStyle(Theme.accent)
                        }
                        VStack(alignment: .leading, spacing: 2) {
                            Text(part.fileName)
                                .font(.subheadline)
                                .lineLimit(1)
                                .truncationMode(.middle)
                            if let size {
                                Text(ByteCountFormatter.string(fromByteCount: Int64(size), countStyle: .file))
                                    .font(.caption2)
                                    .foregroundStyle(Theme.subtleText)
                            }
                        }
                        Image(systemName: "square.and.arrow.up")
                            .font(.caption)
                            .foregroundStyle(Theme.subtleText)
                    }
                    .padding(10)
                    .background(Theme.panel, in: RoundedRectangle(cornerRadius: 12))
                }
                .buttonStyle(.plain)
                .disabled(downloading)
                .accessibilityLabel("Share \(part.fileName)")
            }
        }
        .task(id: "\(part.vaultPath ?? "")#\(attempt)") { await loadSize() }
        .sheet(isPresented: $showShare) {
            if let localURL {
                ActivityShareSheet(items: [localURL])
            }
        }
    }

    private func loadSize() async {
        failed = false
        if let localData {
            size = localData.count
            localURL = await Task.detached(priority: .utility) {
                VaultFileCache.localFileURL(name: part.fileName, data: localData)
            }.value
            return
        }
        guard let client, let path = part.vaultPath, !path.isEmpty else { return }
        size = await client.vaultRawLength(path: path)
    }

    private func downloadAndShare() {
        if localURL != nil {
            showShare = true
            return
        }
        guard let client, let path = part.vaultPath, !path.isEmpty else {
            failed = true
            return
        }
        downloading = true
        Task {
            do {
                let url = try await VaultFileCache.shared.url(for: path, client: client)
                localURL = url
                downloading = false
                showShare = true
            } catch {
                downloading = false
                failed = true
            }
        }
    }

    /// SF Symbol per file family for the chip.
    static func icon(for fileName: String) -> String {
        let ext = (fileName as NSString).pathExtension.lowercased()
        switch ext {
        case "pdf": return "doc.richtext"
        case "zip", "gz", "tar", "rar", "7z": return "doc.zipper"
        case "csv", "xlsx", "xls", "numbers", "tsv": return "tablecells"
        case "txt", "md", "log", "json", "yaml", "yml", "toml", "xml", "html", "css": return "doc.plaintext"
        case "py", "swift", "js", "ts", "go", "rs", "sh", "c", "h", "cpp", "java", "rb", "php": return "chevron.left.forwardslash.chevron.right"
        case "mov", "mp4", "avi", "mkv", "webm", "m4v": return "video"
        case "png", "jpg", "jpeg", "gif", "webp", "heic", "tiff", "bmp", "svg": return "photo"
        case "m4a", "mp3", "wav", "aac", "ogg", "flac": return "waveform"
        default: return "doc"
        }
    }
}

// MARK: - Share sheet

/// UIActivityViewController wrapped for SwiftUI — the document chip
/// presents it inside a .sheet with the downloaded local file.
struct ActivityShareSheet: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}
