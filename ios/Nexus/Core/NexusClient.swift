import Foundation

enum APIError: LocalizedError, Equatable {
    case unauthorized
    case forbidden(String)
    case notFound(String)
    case badStatus(Int, String)
    case unreachable(String)
    case decode(String)

    var errorDescription: String? {
        switch self {
        case .unauthorized:
            return "The server rejected the pairing token. Re-pair with a fresh QR code (Settings → pair)."
        case .forbidden(let detail):
            return detail.isEmpty ? "Not allowed" : detail
        case .notFound(let detail):
            return detail.isEmpty ? "Not found" : detail
        case .badStatus(let code, let body):
            let clipped = String(body.prefix(160))
            return clipped.isEmpty ? "Server error (\(code))" : clipped
        case .unreachable(let detail):
            return "Can't reach the server — check Tailscale is on: \(detail)"
        case .decode(let detail):
            return "Unexpected response: \(detail)"
        }
    }
}

/// Async client for one Nexus server. All requests carry the pairing
/// token as a Bearer header; the base URL is the tailnet address from
/// the QR payload.
final class NexusClient {
    let profile: ServerProfile
    /// REST session: fails fast (15s) so pairing/connectivity problems
    /// surface as readable errors instead of an endless spinner.
    private let session: URLSession
    /// SSE session: long-lived streams (chat, events, browser frames) that
    /// are *expected* to sit idle between server pings.
    private let streamSession: URLSession
    private let decoder = JSONDecoder()

    init(profile: ServerProfile) {
        self.profile = profile
        let rest = URLSessionConfiguration.default
        rest.timeoutIntervalForRequest = 15
        rest.timeoutIntervalForResource = 60
        rest.waitsForConnectivity = false
        self.session = URLSession(configuration: rest)
        let streams = URLSessionConfiguration.default
        // Idle gaps are fine (server pings every 15s); a *request* deadline
        // of 0 = effectively none for streams, resource cap at 24h.
        streams.timeoutIntervalForRequest = 0
        streams.timeoutIntervalForResource = 86_400
        streams.waitsForConnectivity = false
        self.streamSession = URLSession(configuration: streams)
    }

    private func request(_ path: String, method: String = "GET", body: Encodable? = nil, query: [String: String] = [:]) -> URLRequest {
        var components = URLComponents(url: profile.baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty {
            components.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        var req = URLRequest(url: components.url!)
        req.httpMethod = method
        req.setValue("Bearer \(profile.token)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try? JSONEncoder().encode(AnyEncodable(body))
        }
        return req
    }

    private func run(_ req: URLRequest) async throws -> Data {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: req)
        } catch let urlError as URLError {
            // Include the raw code — "ATS requires secure connection" (-1022)
            // vs "cannot connect" (-1004) vs local-network privacy point at
            // completely different fixes.
            throw APIError.unreachable("[\(urlError.code.rawValue)] \(urlError.localizedDescription)")
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError.unreachable("no HTTP response")
        }
        switch http.statusCode {
        case 200...299:
            return data
        case 401:
            throw APIError.unauthorized
        case 403:
            throw APIError.forbidden(String(data: data, encoding: .utf8) ?? "")
        case 404:
            throw APIError.notFound(String(data: data, encoding: .utf8) ?? "")
        default:
            throw APIError.badStatus(http.statusCode, String(data: data, encoding: .utf8) ?? "")
        }
    }

    private func get<T: Decodable>(_ path: String, query: [String: String] = [:]) async throws -> T {
        let data = try await run(request(path, query: query))
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            // String(describing:) includes the key path (keyNotFound /
            // typeMismatch name the exact field); localizedDescription alone
            // is just "isn't in the correct format".
            throw APIError.decode("\(T.self): \(String(describing: error).prefix(300))")
        }
    }

    private func send<T: Decodable>(_ path: String, method: String, body: Encodable? = nil) async throws -> T {
        let data = try await run(request(path, method: method, body: body))
        return try decoder.decode(T.self, from: data)
    }

    // MARK: - Handshake / bots / sessions

    func status() async throws -> StatusInfo { try await get("status") }

    func bots() async throws -> [Bot] { try await get("bots") }

    func sessions(limit: Int = 100, projectId: String? = nil) async throws -> [SessionSummary] {
        var query = ["limit": String(limit)]
        if let projectId { query["project_id"] = projectId }
        return try await get("sessions", query: query)
    }

    func session(_ id: String) async throws -> ChatSession { try await get("sessions/\(id)") }

    /// PATCH /sessions/{id}/archive — hide a chat from the roster and
    /// history lists. Fire-and-forget on the call sites, but the client
    /// method surfaces transport/auth errors instead of duplicating the
    /// auth header by hand.
    func archiveSession(id: String) async throws {
        struct EmptyBody: Encodable {}
        _ = try await run(request("sessions/\(id)/archive", method: "PATCH", body: EmptyBody()))
    }

    // MARK: - Chat

    struct SendMessageBody: Encodable {
        var sessionId: String?
        var message: String
        var attachments: [ChatAttachment]?
        var projectId: String?

        enum CodingKeys: String, CodingKey {
            case message, attachments
            case sessionId = "session_id"
            case projectId = "project_id"
        }
    }

    /// POST /chat/stream → decoded SSE events. A long request timeout is
    /// used so a running turn never hits it.
    func chatStream(
        sessionId: String?,
        message: String,
        attachments: [ChatAttachment]? = nil,
        projectId: String? = nil
    ) -> AsyncThrowingStream<ChatStreamEvent, Error> {
        var req = request("chat/stream", method: "POST", body: SendMessageBody(
            sessionId: sessionId, message: message, attachments: attachments, projectId: projectId
        ))
        req.timeoutInterval = 60 * 30
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        return decoded(streamSession.sseEvents(for: req)) { ev in
            ChatStreamEvent.decode(event: ev.event, data: ev.data)
        }
    }

    /// Re-attach to a running turn's stream (after backgrounding).
    func turnStream(sessionId: String) -> AsyncThrowingStream<ChatStreamEvent, Error> {
        let req = request("chat/\(sessionId)/turn/stream")
        return decoded(streamSession.sseEvents(for: req)) { ev in
            ChatStreamEvent.decode(event: ev.event, data: ev.data)
        }
    }

    /// Map SSE events onto a concrete element type, preserving cancellation.
    private func decoded<T>(
        _ base: AsyncThrowingStream<SSEEvent, Error>,
        _ transform: @escaping (SSEEvent) -> T
    ) -> AsyncThrowingStream<T, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    for try await ev in base {
                        continuation.yield(transform(ev))
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    struct ActiveTurnResponse: Decodable {
        // Server returns {"running": true, "started_at": ...} — "active" was
        // a legacy guess that never matched and made every check return false.
        var running: Bool?
    }

    func activeTurn(sessionId: String) async throws -> Bool {
        let r: ActiveTurnResponse = try await get("chat/\(sessionId)/turn/active")
        return r.running ?? false
    }

    func cancelTurn(sessionId: String) async throws {
        _ = try await run(request("chat/\(sessionId)/cancel", method: "POST"))
    }

    // MARK: - Vault upload

    /// POST /vault/upload (multipart) → vault-relative path of the stored
    /// file. Raw multipart encoding by hand — URLSession has no multipart
    /// encoder and we only need the single `files` field the server reads.
    func uploadFile(data: Data, filename: String, mimeType: String) async throws -> String {
        let boundary = UUID().uuidString
        var req = request("vault/upload", method: "POST")
        // Photos/PDFs over Tailscale can be a few MB — give it room beyond
        // the REST session's fast-fail 15s default.
        req.timeoutInterval = 60
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        func append(_ s: String) { body.append(Data(s.utf8)) }
        append("\r\n--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"files\"; filename=\"\(filename)\"\r\n")
        append("Content-Type: \(mimeType)\r\n\r\n")
        body.append(data)
        append("\r\n--\(boundary)--\r\n")
        req.httpBody = body

        let data = try await run(req)
        struct UploadResult: Decodable {
            struct Uploaded: Decodable { var path: String }
            var uploaded: [Uploaded]
        }
        let result: UploadResult
        do {
            result = try decoder.decode(UploadResult.self, from: data)
        } catch {
            throw APIError.decode("upload reply: \(String(describing: error).prefix(300))")
        }
        guard let path = result.uploaded.first?.path, !path.isEmpty else {
            throw APIError.decode("upload reply stored no file")
        }
        return path
    }

    // MARK: - HITL

    struct PendingResponse: Decodable { var pending: PendingRequest? }

    func pendingRequest(sessionId: String) async throws -> PendingRequest? {
        let r: PendingResponse = try await get("chat/\(sessionId)/pending")
        return r.pending
    }

    struct RespondBody: Encodable {
        var requestId: String
        var answer: String

        enum CodingKeys: String, CodingKey {
            case answer
            case requestId = "request_id"
        }
    }

    func respond(sessionId: String, requestId: String, answer: String) async throws {
        struct OK: Decodable { var ok: Bool? }
        let _: OK = try await send("chat/\(sessionId)/respond", method: "POST", body: RespondBody(requestId: requestId, answer: answer))
    }

    /// POST /chat/{sid}/hitl/{request_id}/cancel — dismiss a pending
    /// human-in-the-loop prompt without answering it.
    func hitlCancel(sessionId: String, requestId: String) async throws {
        _ = try await run(request("chat/\(sessionId)/hitl/\(requestId)/cancel", method: "POST"))
    }

    // MARK: - Browser

    func browserInstances() async throws -> [BrowserInstanceStatus] { try await get("browser/instances") }

    func browserStream(key: String) -> AsyncThrowingStream<BrowserStreamFrame, Error> {
        let req = request("browser/stream", query: ["key": key])
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    for try await ev in streamSession.sseEvents(for: req) {
                        if let frame = BrowserStreamFrame.decode(ev.data) {
                            continuation.yield(frame)
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    struct BrowserActionBody: Encodable {
        var key: String
        var action: String
        var url: String?
        var text: String?
        var keyName: String?
        var xNorm: Double?
        var yNorm: Double?
        var deltaY: Double?

        enum CodingKeys: String, CodingKey {
            case key, action, url, text
            case keyName = "key_name"
            case xNorm = "x_norm"
            case yNorm = "y_norm"
            case deltaY = "delta_y"
        }
    }

    @discardableResult
    func browserAction(key: String, action: String, url: String? = nil, text: String? = nil,
                       keyName: String? = nil, xNorm: Double? = nil, yNorm: Double? = nil,
                       deltaY: Double? = nil) async throws -> BrowserActionResult {
        try await send("browser/action", method: "POST", body: BrowserActionBody(
            key: key, action: action, url: url, text: text, keyName: keyName,
            xNorm: xNorm, yNorm: yNorm, deltaY: deltaY
        ))
    }

    func browserStop(key: String) async throws {
        struct Body: Encodable { var key: String }
        struct OK: Decodable { var stopped: Bool? }
        let _: OK = try await send("browser/stop", method: "POST", body: Body(key: key))
    }

    // MARK: - Vault (read-only)

    func vaultTree() async throws -> [VaultTreeEntry] { try await get("vault/tree") }

    func vaultFile(path: String) async throws -> VaultFile {
        try await get("vault/file", query: ["path": path])
    }

    func vaultSearch(_ q: String) async throws -> VaultSearchResponse {
        try await get("vault/search", query: ["q": q, "limit": "30"])
    }

    // MARK: - Push registration

    struct ApnsRegisterBody: Encodable {
        var token: String
        var bundleId: String
        var environment: String
    }

    struct ApnsRegisterResponse: Decodable {
        var ok: Bool?
        var deliverable: Bool?
    }

    @discardableResult
    func registerApns(token: String) async throws -> ApnsRegisterResponse {
        let env: String
        #if DEBUG
        env = "sandbox"
        #else
        env = "production"
        #endif
        return try await send("push/apns/register", method: "POST", body: ApnsRegisterBody(
            token: token, bundleId: Bundle.main.bundleIdentifier ?? "com.nexus.app", environment: env
        ))
    }

    // MARK: - Session events channel (HITL + queue)

    /// GET /chat/{sid}/events — long-lived; frames carry `event:` names.
    func sessionEvents(sessionId: String) -> AsyncThrowingStream<SSEEvent, Error> {
        let req = request("chat/\(sessionId)/events")
        return streamSession.sseEvents(for: req)
    }
}

/// Type-erasing box so request() can take any Encodable body.
private struct AnyEncodable: Encodable {
    private let encodeFunc: (Encoder) throws -> Void
    init(_ wrapped: Encodable) {
        encodeFunc = wrapped.encode
    }
    func encode(to encoder: Encoder) throws {
        try encodeFunc(encoder)
    }
}
