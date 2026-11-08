import SwiftUI
import AVFoundation

// MARK: - QR scanner (AVFoundation metadata capture)

/// Full-screen QR scanner. Uses AVCaptureMetadataOutput (works on every
/// iOS 16+ device; no DataScanner dependencies).
final class QRScannerController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    var onCode: (String) -> Void = { _ in }

    private let session = AVCaptureSession()
    private var previewLayer: AVCaptureVideoPreviewLayer?
    private var lastEmitted = ""

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        guard let device = AVCaptureDevice.default(for: .video),
              let input = try? AVCaptureDeviceInput(device: device),
              session.canAddInput(input) else {
                showFailure("Camera unavailable")
                return
        }
        session.addInput(input)
        let output = AVCaptureMetadataOutput()
        guard session.canAddOutput(output) else {
            showFailure("Capture unavailable")
            return
        }
        session.addOutput(output)
        output.setMetadataObjectsDelegate(self, queue: .main)
        output.metadataObjectTypes = [.qr]
        let layer = AVCaptureVideoPreviewLayer(session: session)
        layer.videoGravity = .resizeAspectFill
        view.layer.addSublayer(layer)
        previewLayer = layer
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        previewLayer?.frame = view.bounds
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        DispatchQueue.global(qos: .userInitiated).async { [session] in
            if !session.isRunning { session.startRunning() }
        }
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        DispatchQueue.global(qos: .userInitiated).async { [session] in
            if session.isRunning { session.stopRunning() }
        }
    }

    private func showFailure(_ message: String) {
        let label = UILabel()
        label.text = message
        label.textColor = .white
        label.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(label)
        NSLayoutConstraint.activate([
            label.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            label.centerYAnchor.constraint(equalTo: view.centerYAnchor),
        ])
    }

    func metadataOutput(
        _ output: AVCaptureMetadataOutput,
        didOutput metadataObjects: [AVMetadataObject],
        from connection: AVCaptureConnection
    ) {
        guard let obj = metadataObjects.first as? AVMetadataMachineReadableCodeObject,
              obj.type == .qr,
              let value = obj.stringValue,
              value != lastEmitted else { return }
        lastEmitted = value
        AudioServicesPlaySystemSound(SystemSoundID(kSystemSoundID_Vibrate))
        onCode(value)
    }
}

struct QRScannerView: UIViewControllerRepresentable {
    var onCode: (String) -> Void

    func makeUIViewController(context: Context) -> QRScannerController {
        let vc = QRScannerController()
        vc.onCode = onCode
        return vc
    }

    func updateUIViewController(_ vc: QRScannerController, context: Context) {}
}

// MARK: - Pairing flow

/// Entry: scan the QR from Settings → Native app pairing (or `nexus pair`),
/// or enter the values manually.
struct PairingView: View {
    var scannedProfile: ServerProfile?
    var onDone: (ServerProfile) -> Void

    @State private var mode: Mode = .scan
    @State private var manualName = ""
    @State private var manualHost = ""
    @State private var manualPort = "443"
    @State private var manualToken = ""
    @State private var manualTLS = true
    @State private var confirming: ServerProfile?
    @Environment(\.dismiss) private var dismiss

    enum Mode: String, CaseIterable, Identifiable {
        case scan = "Scan QR"
        case manual = "Manual"
        var id: String { rawValue }
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                Picker("Mode", selection: $mode) {
                    ForEach(Mode.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .padding()

                switch mode {
                case .scan:
                    scanPane
                case .manual:
                    manualPane
                }
            }
            .navigationTitle("Pair a server")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .sheet(item: $confirming) { profile in
                PairingConfirmView(profile: profile) { paired in
                    confirming = nil
                    onDone(paired)
                }
            }
            .onAppear {
                // Camera-scanned or deep-linked profile arrives here.
                if let scannedProfile { confirming = scannedProfile }
            }
            .onChange(of: scannedProfile) { _, newValue in
                if let newValue { confirming = newValue }
            }
        }
    }

    private var scanPane: some View {
        ZStack(alignment: .bottom) {
            QRScannerView { code in
                guard let url = URL(string: code),
                      let profile = ServerProfile.fromDeepLink(url) else { return }
                confirming = profile
            }
            .ignoresSafeArea(edges: .bottom)
            Text("Point the camera at the QR in Settings → Native app pairing")
                .font(.footnote)
                .padding(10)
                .frame(maxWidth: .infinity)
                .background(.ultraThinMaterial)
        }
    }

    private var manualPane: some View {
        Form {
            Section("Server (from `nexus pair` or the pairing card)") {
                TextField("Machine name", text: $manualName)
                TextField("Host (nino-max.xxx.ts.net or 100.x.y.z)", text: $manualHost)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                TextField("Port", text: $manualPort)
                    .keyboardType(.numberPad)
                Toggle("HTTPS", isOn: $manualTLS)
            }
            Section("Token") {
                TextField("Pairing token", text: $manualToken)
                    .font(.system(.footnote, design: .monospaced))
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            }
            Button("Connect") {
                guard let port = Int(manualPort), !manualHost.isEmpty, !manualToken.isEmpty else { return }
                confirming = ServerProfile(
                    name: manualName.isEmpty ? manualHost : manualName,
                    host: manualHost,
                    port: port,
                    token: manualToken,
                    tls: manualTLS
                )
            }
            .disabled(manualHost.isEmpty || manualToken.isEmpty)
        }
        .scrollDismissesKeyboard(.interactively)
    }
}

/// Confirms the machine identity over GET /status before saving anything.
struct PairingConfirmView: View {
    let profile: ServerProfile
    var onDone: (ServerProfile) -> Void

    @State private var status: StatusInfo?
    @State private var error: String?
    @State private var working = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                Image(systemName: "cpu")
                    .font(.system(size: 48))
                    .foregroundStyle(Theme.accent)
                VStack(spacing: 6) {
                    Text(status?.machineName ?? profile.name)
                        .font(.title2.bold())
                    Text(profile.baseURL.absoluteString)
                        .font(.subheadline.monospaced())
                        .foregroundStyle(Theme.subtleText)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    if let version = status?.version {
                        Text("Nexus \(version)")
                            .font(.caption)
                            .foregroundStyle(Theme.subtleText)
                    }
                }
                if let error {
                    ErrorBanner(message: error)
                        .padding(.horizontal)
                }
                if working {
                    ProgressView("Verifying token…")
                } else if status != nil {
                    Button("Connect") {
                        onDone(profile)
                    }
                    .buttonStyle(.borderedProminent)
                } else {
                    Button("Retry") { Task { await verify() } }
                        .buttonStyle(.bordered)
                }
                Spacer()
            }
            .padding(.top, 60)
            .navigationTitle("Pair device")
            .navigationBarTitleDisplayMode(.inline)
        }
        .task { await verify() }
        .presentationDetents([.medium, .large])
    }

    private func verify() async {
        working = true
        error = nil
        defer { working = false }
        do {
            status = try await NexusClient(profile: profile).status()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
