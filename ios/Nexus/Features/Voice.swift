import AVFoundation
import CryptoKit
import SwiftUI
import UIKit

// Phase 3 of the iOS UX plan — voice. Three independent pieces:
//
// * TTS playback of bot replies (contextMenu "Speak") — POST
//   /tts/synthesize returns the synthesized audio bytes; they are cached
//   on disk keyed by SHA-256(text) following the same pattern as
//   VaultFileCache, and played with AVAudioPlayer. A server without the
//   TTS routes (404/501/405) silently hides the option.
// * Transcript under received audio parts (used from AudioPartView) —
//   GET /vault/transcribe on demand, cached on disk.
// * Hold-to-talk voice memo — AVAudioRecorder → m4a → staged through the
//   regular attachment flow.

// MARK: - TTS disk cache

/// Disk cache for synthesized speech. Keys are the SHA-256 of the exact
/// text (voice/speed are the server defaults everywhere on iOS, so text
/// alone identifies the audio). Piper returns WAV; even if the server
/// ever changes container, AudioToolbox sniffs the file content rather
/// than the extension when AVAudioPlayer opens it.
actor TTSCache {
    static let shared = TTSCache()

    private let dir: URL
    /// Two bubbles speaking the same text (or a double tap racing a
    /// download) must hit the wire once.
    private var inFlight: [String: Task<URL, Error>] = [:]

    private init() {
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        dir = caches.appendingPathComponent("tts-audio", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    /// Cached audio for a text, if present — no network.
    func cachedURL(for text: String) -> URL? {
        let url = Self.keyedURL(text)
        return (try? url.checkResourceIsReachable()) == true ? url : nil
    }

    /// Resolve text to a playable file URL: cache hit, or an
    /// authenticated POST /tts/synthesize written into the cache.
    func url(for text: String, client: NexusClient) async throws -> URL {
        if let cached = cachedURL(for: text) { return cached }
        let key = Self.key(text)
        if let running = inFlight[key] {
            inFlight[key] = nil
            return try await running.value
        }
        let task = Task<URL, Error> { [client] in
            let data = try await client.ttsAudio(text: text)
            let url = Self.keyedURL(text)
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

    private static func key(_ text: String) -> String {
        SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    private static func keyedURL(_ text: String) -> URL {
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        let dir = caches.appendingPathComponent("tts-audio", isDirectory: true)
        return dir.appendingPathComponent(key(text) + ".wav")
    }
}

// MARK: - TTS playback

/// Shared speaker for the chat: one message plays at a time, tapping
/// Speak again on it (or on another message) stops the previous playback.
/// The view reads `unavailable` to hide the option on servers without
/// TTS and `speakingKey` to swap the menu label to "Stop".
@MainActor
final class TTSSpeaker: NSObject, ObservableObject, AVAudioPlayerDelegate {
    static let shared = TTSSpeaker()

    /// Set once the server proves it has no TTS (404/501/405 on the
    /// synthesize call) — the Speak item then disappears everywhere.
    @Published private(set) var unavailable = false
    /// Key of the message currently spoken (set while synthesizing too,
    /// so the tap-twice-quickly path lands on "stop" semantics).
    @Published private(set) var speakingKey: String?

    private var player: AVAudioPlayer?

    /// Speak a message, or stop it if it is the one playing. Never
    /// surfaces errors: a failed synth just leaves the menu unchanged
    /// (and hides it forever when the server lacks the route).
    func toggle(key: String, text: String, client: NexusClient?) {
        if speakingKey == key {
            stop()
            return
        }
        stop()
        guard let client, !text.isEmpty else { return }
        speakingKey = key
        Task {
            do {
                let url = try await TTSCache.shared.url(for: text, client: client)
                // The user may have tapped another message meanwhile.
                guard speakingKey == key else { return }
                let p = try AVAudioPlayer(contentsOf: url)
                p.delegate = self
                p.prepareToPlay()
                p.play()
                player = p
            } catch {
                if Self.isMissing(error) {
                    unavailable = true
                }
                if speakingKey == key { speakingKey = nil }
            }
        }
    }

    func stop() {
        player?.stop()
        player = nil
        speakingKey = nil
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            if self.player === player { self.stop() }
        }
    }

    /// The TTS routes absent from this server build — as opposed to a
    /// transient synth failure (500/502), which keeps the option.
    private static func isMissing(_ error: Error) -> Bool {
        switch error as? APIError {
        case .notFound:
            return true
        case .badStatus(let code, _) where code == 501 || code == 405 || code == 404:
            return true
        default:
            return false
        }
    }
}

// MARK: - Audio transcripts

/// Disk-backed cache for /vault/transcribe results. The server memoizes
/// per (path, mtime) itself, but this keeps repeat taps on the same
/// bubble (and app restarts within the cache lifetime) off the wire —
/// same SHA-256-of-path convention as VaultFileCache.
actor AudioTranscriber {
    static let shared = AudioTranscriber()

    private let dir: URL
    private var memory: [String: String] = [:]
    private var inFlight: [String: Task<String, Error>] = [:]

    private init() {
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        dir = caches.appendingPathComponent("vault-files", isDirectory: true)
            .appendingPathComponent("transcripts", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    /// Transcript of a vault audio file; an empty string means the
    /// transcription produced no text (callers treat it as "none").
    func transcript(for vaultPath: String, client: NexusClient) async throws -> String {
        if let hit = memory[vaultPath] { return hit }
        let file = keyedURL(vaultPath)
        if let cached = try? String(contentsOf: file, encoding: .utf8) {
            memory[vaultPath] = cached
            return cached
        }
        let key = Self.key(vaultPath)
        if let running = inFlight[key] {
            inFlight[key] = nil
            return try await running.value
        }
        let task = Task<String, Error> { [client] in
            let text = try await client.transcribeVaultAudio(path: vaultPath)
            try? text.write(to: file, atomically: true, encoding: .utf8)
            return text
        }
        inFlight[key] = task
        do {
            let text = try await task.value
            inFlight[key] = nil
            memory[vaultPath] = text
            return text
        } catch {
            inFlight[key] = nil
            throw error
        }
    }

    private static func key(_ path: String) -> String {
        SHA256.hash(data: Data(path.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    private func keyedURL(_ path: String) -> URL {
        dir.appendingPathComponent(Self.key(path) + ".txt")
    }
}

// MARK: - Voice memo recorder

/// Hold-to-talk engine around AVAudioRecorder. One recording at a time;
/// the hold gesture decides send vs discard, and very short takes
/// (accidental taps) are dropped. Output is AAC in an .m4a container so
/// the staged attachment flows through the regular upload path and the
/// server's audio classification picks it up.
@MainActor
final class VoiceMemoRecorder: NSObject, ObservableObject, AVAudioRecorderDelegate {
    @Published private(set) var recording = false
    /// Seconds since the hold started — drives the recording badge.
    @Published private(set) var elapsed: TimeInterval = 0
    /// True after an explicit denial — the mic button becomes a
    /// shortcut to the iOS Settings app.
    @Published private(set) var permissionDenied = false

    /// Called with (filename, bytes) when a recording is released to
    /// send. Never called for cancelled or too-short takes.
    private var onSend: ((String, Data) -> Void)?
    /// The hold is still down — a permission prompt granted *after* the
    /// finger lifted must not start a phantom recording.
    private var holdActive = false
    private var recorder: AVAudioRecorder?
    private var fileURL: URL?
    private var fileName: String?
    private var startedAt: Date?
    private var timer: Timer?

    /// Arm a hold. The gesture calls this on first touch; the completion
    /// fires on release-for-send with the finished recording.
    func begin(onSend: @escaping (String, Data) -> Void) {
        guard !recording, holdActive != true else { return }
        // Recording while speaking a reply would interleave both streams.
        TTSSpeaker.shared.stop()
        self.onSend = onSend
        holdActive = true
        switch AVAudioApplication.shared.recordPermission {
        case .denied:
            permissionDenied = true
            holdActive = false
        case .undetermined:
            AVAudioApplication.requestRecordPermission { [weak self] granted in
                Task { @MainActor in
                    guard let self else { return }
                    self.holdActive = false
                    if granted {
                        self.startRecording()
                    } else {
                        self.permissionDenied = true
                    }
                }
            }
        case .granted:
            startRecording()
        @unknown default:
            holdActive = false
        }
    }

    /// Finger lifted. `send` false (or a take shorter than the
    /// accidental-tap threshold) discards the file without calling back.
    func end(send: Bool) {
        holdActive = false
        guard recording, let recorder else { return }
        let duration = elapsed
        let url = recorder.url
        let name = fileName
        recording = false
        timer?.invalidate()
        timer = nil
        recorder.stop()
        self.recorder = nil
        let data = try? Data(contentsOf: url)
        try? FileManager.default.removeItem(at: url)
        fileURL = nil
        fileName = nil
        startedAt = nil
        elapsed = 0
        guard send, duration >= Self.minDuration, let data, !data.isEmpty, let name else {
            onSend = nil
            return
        }
        onSend?(name, data)
        onSend = nil
    }

    /// Sub-half-second holds are accidental taps, not memos.
    static let minDuration: TimeInterval = 0.6

    private func startRecording() {
        guard holdActive else { return }
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker])
        try? session.setActive(true)
        let name = Self.newFileName()
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(name)
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: 44_100,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
        ]
        guard let r = try? AVAudioRecorder(url: url, settings: settings) else {
            holdActive = false
            return
        }
        r.delegate = self
        r.record()
        recorder = r
        fileURL = url
        fileName = name
        startedAt = Date()
        elapsed = 0
        recording = true
        timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, let startedAt = self.startedAt else { return }
                self.elapsed = Date().timeIntervalSince(startedAt)
            }
        }
    }

    /// Timestamped name matching the photo convention (Staging.photoName).
    private static func newFileName() -> String {
        let stamp = ISO8601DateFormatter().string(from: Date())
            .replacingOccurrences(of: ":", with: "-")
            .replacingOccurrences(of: "+", with: "z")
        return "voice-\(stamp).m4a"
    }

    nonisolated func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder, successfully flag: Bool) {
        // end() drives the lifecycle; nothing to do here.
    }
}

// MARK: - Hold-to-talk button

/// The composer's mic: press and hold to record, release to send, drag
/// up to cancel (iMessage/WhatsApp convention). Denied permission turns
/// a tap on it into a Settings shortcut.
struct VoiceMemoButton: View {
    @ObservedObject var recorder: VoiceMemoRecorder
    /// Composer has text/attachments (or a turn is streaming) — no
    /// recording then, which also forbids typing while recording because
    /// the text view is disabled while `recorder.recording`.
    var disabled: Bool
    var onSend: (String, Data) -> Void

    @State private var cancelZone = false
    @State private var beginAttempted = false

    var body: some View {
        Group {
            if recorder.permissionDenied {
                micIcon
                    .onTapGesture { Self.openSettings() }
            } else {
                micIcon
                    .gesture(holdGesture)
                    .overlay(alignment: .bottom) {
                        if cancelZone, recorder.recording {
                            Text("Release to cancel")
                                .font(.caption2.weight(.semibold))
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                                .background(.red.opacity(0.85), in: Capsule())
                                .foregroundStyle(.white)
                                .fixedSize()
                                .offset(y: -44)
                                .transition(.opacity)
                        }
                    }
            }
        }
        .opacity(disabled && !recorder.recording ? 0.4 : 1)
        .animation(.easeInOut(duration: 0.15), value: cancelZone)
    }

    private var micIcon: some View {
        Image(systemName: recorder.recording
            ? "mic.fill"
            : (recorder.permissionDenied ? "mic.slash" : "mic"))
            .font(.system(size: 22))
            .foregroundStyle(recorder.recording ? Color.red : Theme.accent)
            .accessibilityLabel(recorder.recording ? "Recording — release to send" : "Record voice memo")
    }

    private var holdGesture: some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { g in
                guard !disabled else { return }
                if !beginAttempted {
                    beginAttempted = true
                    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                    recorder.begin { name, data in onSend(name, data) }
                }
                let up = g.translation.height < -60
                if up != cancelZone {
                    withAnimation(.easeInOut(duration: 0.15)) { cancelZone = up }
                    if up {
                        UINotificationFeedbackGenerator().notificationOccurred(.warning)
                    }
                }
            }
            .onEnded { g in
                beginAttempted = false
                let send = recorder.recording && cancelZone == false
                    && g.translation.height < 60
                if cancelZone {
                    withAnimation(.easeInOut(duration: 0.15)) { cancelZone = false }
                }
                recorder.end(send: send)
            }
    }

    static func openSettings() {
        guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
        UIApplication.shared.open(url)
    }
}

// MARK: - Recording indicator

/// Bar shown above the composer while holding: pulsing dot, elapsed
/// time, animated bars — a cheap stand-in for a live waveform meter.
struct RecordingIndicator: View {
    let elapsed: TimeInterval

    var body: some View {
        HStack(spacing: 10) {
            RecordingPulseDot()
            Text(Self.clock(elapsed))
                .font(.caption.monospacedDigit().weight(.semibold))
                .foregroundStyle(.red)
            RecordingBars()
            Text("Release to send · slide up to cancel")
                .font(.caption)
                .foregroundStyle(Theme.subtleText)
                .lineLimit(1)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .background(.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
    }

    private static func clock(_ t: TimeInterval) -> String {
        let s = Int(t.rounded())
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}

/// Red dot that breathes while the recorder runs.
struct RecordingPulseDot: View {
    @State private var pulse = false

    var body: some View {
        Circle()
            .fill(.red)
            .frame(width: 10, height: 10)
            .scaleEffect(pulse ? 1.35 : 0.85)
            .opacity(pulse ? 1 : 0.6)
            .animation(.easeInOut(duration: 0.55).repeatForever(autoreverses: true), value: pulse)
            .onAppear { pulse = true }
    }
}

/// Four bars bouncing out of phase — the "waveform" of the badge.
struct RecordingBars: View {
    @State private var on = false

    var body: some View {
        HStack(spacing: 3) {
            ForEach(0..<4, id: \.self) { i in
                RoundedRectangle(cornerRadius: 1.5)
                    .fill(.red.opacity(0.75))
                    .frame(width: 3, height: on ? 14 : 5)
                    .animation(
                        .easeInOut(duration: 0.32).repeatForever(autoreverses: true)
                            .delay(Double(i) * 0.09),
                        value: on
                    )
            }
        }
        .onAppear { on = true }
    }
}
