import Foundation

/// Spawns and supervises the bundled Python server.
///
/// Resources layout (resolved relative to the .app bundle):
///   Contents/Resources/python/bin/python3
///   Contents/Resources/bootstrap.py
///   Contents/Resources/.port    (written by bootstrap once the port is chosen)
final class ServerController {
    private var process: Process?
    private(set) var port: Int?
    private(set) var bindHost: String = "127.0.0.1"

    enum LaunchError: Error, LocalizedError {
        case missingResource(String)
        case timeout
        case nonZeroExit(Int32)

        var errorDescription: String? {
            switch self {
            case .missingResource(let r): return "Missing bundled resource: \(r)"
            case .timeout:                return "Server did not respond on /health in time"
            case .nonZeroExit(let c):     return "Server exited with code \(c)"
            }
        }
    }

    func launch() throws {
        guard process == nil else { return }
        let resources = Bundle.main.resourceURL ?? Bundle.main.bundleURL
        let python = resources.appendingPathComponent("python/bin/python3")
        let bootstrap = resources.appendingPathComponent("bootstrap.py")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            throw LaunchError.missingResource(python.path)
        }
        guard FileManager.default.fileExists(atPath: bootstrap.path) else {
            throw LaunchError.missingResource(bootstrap.path)
        }

        let logDir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/Nexus")
        try? FileManager.default.createDirectory(at: logDir, withIntermediateDirectories: true)
        let logURL = logDir.appendingPathComponent("server.log")
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        let logHandle = try FileHandle(forWritingTo: logURL)
        logHandle.seekToEndOfFile()

        // Clear any stale port file from a previous run so waitForReady reads a fresh one.
        let portFile = resources.appendingPathComponent(".port")
        try? FileManager.default.removeItem(at: portFile)

        let p = Process()
        p.executableURL = python
        p.arguments = [bootstrap.path]
        p.standardOutput = logHandle
        p.standardError = logHandle
        var env = ProcessInfo.processInfo.environment
        env["NEXUS_PORT_FILE"] = portFile.path
        // Tell Python where its own home is — python-build-standalone uses this.
        env["PYTHONHOME"] = resources.appendingPathComponent("python").path
        p.environment = env

        try p.run()
        self.process = p
    }

    /// Polls the .port file then GET /health until success or timeout.
    func waitForReady(timeout seconds: Double) async throws -> Int {
        let deadline = Date().addingTimeInterval(seconds)
        let resources = Bundle.main.resourceURL ?? Bundle.main.bundleURL
        let portFile = resources.appendingPathComponent(".port")

        let hostFile = resources.appendingPathComponent(".host")
        while Date() < deadline {
            if let port = readPort(at: portFile) {
                self.port = port
                if let h = try? String(contentsOf: hostFile, encoding: .utf8) {
                    self.bindHost = h.trimmingCharacters(in: .whitespacesAndNewlines)
                }
                if await probe(port: port) { return port }
            }
            try? await Task.sleep(nanoseconds: 250_000_000)
        }
        throw LaunchError.timeout
    }

    private func readPort(at url: URL) -> Int? {
        guard let data = try? Data(contentsOf: url),
              let s = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
              let p = Int(s) else { return nil }
        return p
    }

    private func probe(port: Int) async -> Bool {
        var req = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/health")!)
        req.timeoutInterval = 1.5
        do {
            let (_, resp) = try await URLSession.shared.data(for: req)
            return (resp as? HTTPURLResponse)?.statusCode == 200
        } catch {
            return false
        }
    }

    func terminate() {
        guard let p = process else { return }
        p.terminate()
        let killDeadline = Date().addingTimeInterval(5)
        while p.isRunning && Date() < killDeadline {
            Thread.sleep(forTimeInterval: 0.1)
        }
        if p.isRunning {
            kill(p.processIdentifier, SIGKILL)
        }
        process = nil
        port = nil
    }
}
