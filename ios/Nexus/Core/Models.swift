import Foundation

// MARK: - Server profile

struct ServerProfile: Codable, Identifiable, Equatable, Hashable {
    var id: UUID = UUID()
    var name: String
    var host: String
    var port: Int
    var token: String
    /// HTTPS endpoint (tailscale serve) — required on iOS 26, whose hardened
    /// ATS rejects cleartext to Tailscale's CGNAT range even with exemptions.
    var tls: Bool = false

    var baseURL: URL {
        let scheme = tls ? "https" : "http"
        let suffix = (tls && port == 443) ? "" : ":\(port)"
        return URL(string: "\(scheme)://\(host)\(suffix)") ?? URL(string: "http://127.0.0.1:18989")!
    }

    /// Deep-link form: `nexus://pair?n=<name>&h=<host>&p=<port>&t=<token>&tls=1`
    static func fromDeepLink(_ url: URL) -> ServerProfile? {
        guard url.scheme?.lowercased() == "nexus",
              url.host?.lowercased() == "pair" else { return nil }
        let q = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        func item(_ name: String) -> String? {
            q.first(where: { $0.name == name })?.value
        }
        guard let host = item("h"), !host.isEmpty,
              let token = item("t"), !token.isEmpty else { return nil }
        let port = Int(item("p") ?? "") ?? (item("tls") == "1" ? 443 : 18989)
        let name = item("n") ?? host
        let tls = item("tls") == "1"
        return ServerProfile(name: name, host: host, port: port, token: token, tls: tls)
    }
}

// MARK: - Handshake (/status)

struct ServerFeatures: Codable, Equatable {
    var browser: Bool?
    var browserRemoteControl: Bool?
    var coordinator: Bool?
    var telegram: Bool?
    var pushApns: Bool?

    enum CodingKeys: String, CodingKey {
        case browser
        case browserRemoteControl = "browser_remote_control"
        case coordinator
        case telegram
        case pushApns = "push_apns"
    }
}

struct StatusInfo: Codable, Equatable {
    var ok: Bool
    var version: String?
    var machineName: String?
    var tailnetIp: String?
    var port: Int?
    var features: ServerFeatures?

    enum CodingKeys: String, CodingKey {
        case ok, version, port, features
        case machineName = "machine_name"
        case tailnetIp = "tailnet_ip"
    }
}

// MARK: - Bots

struct Bot: Codable, Identifiable, Equatable, Hashable {
    var id: String
    var kind: String          // "coordinator" | "bot"
    var name: String
    var handle: String
    var description: String?
    var persona: String?
    var color: String?
    var icon: String?
    var sessionId: String?
    var sessionCount: Int?
    var vaultPath: String?
    var createdAt: Double?
    var updatedAt: Double?

    enum CodingKeys: String, CodingKey {
        case id, kind, name, handle, description, persona, color, icon
        case sessionId = "session_id"
        case sessionCount = "session_count"
        case vaultPath = "vault_path"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    var isCoordinator: Bool { kind == "coordinator" }

    /// The browser context key for this bot ("main" | "project:<id>").
    var browserKey: String { isCoordinator || id == "main" ? "main" : "project:\(id)" }
}

// MARK: - Sessions & messages

struct SessionSummary: Codable, Identifiable, Equatable, Hashable {
    var id: String
    var title: String
    var createdAt: Double?
    var updatedAt: Double?
    var messageCount: Int?
    var projectId: String?
    var archived: Bool?
    var origin: SessionOrigin?
    var team: TeamInfo?

    enum CodingKeys: String, CodingKey {
        case id, title, archived, origin, team
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case messageCount = "message_count"
        case projectId = "project_id"
    }

    init(
        id: String,
        title: String,
        createdAt: Double? = nil,
        updatedAt: Double? = nil,
        messageCount: Int? = nil,
        projectId: String? = nil,
        archived: Bool? = nil,
        origin: SessionOrigin? = nil,
        team: TeamInfo? = nil
    ) {
        self.id = id
        self.title = title
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.messageCount = messageCount
        self.projectId = projectId
        self.archived = archived
        self.origin = origin
        self.team = team
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = (try? c.decode(String.self, forKey: .title)) ?? ""
        createdAt = Lenient.epochValue(c, .createdAt)
        updatedAt = Lenient.epochValue(c, .updatedAt)
        messageCount = Lenient.intValue(c, .messageCount)
        projectId = try? c.decode(String.self, forKey: .projectId)
        archived = Lenient.boolValue(c, .archived)
        origin = try? c.decode(SessionOrigin.self, forKey: .origin)
        team = try? c.decode(TeamInfo.self, forKey: .team)
    }
}

struct SessionOrigin: Codable, Equatable, Hashable {
    var kind: String?
    var source: String?
    var runId: String?

    enum CodingKeys: String, CodingKey {
        case kind, source
        case runId = "run_id"
    }
}

struct TeamInfo: Codable, Equatable, Hashable {
    var name: String?
    var members: [String]?
    var mode: String?
}

/// One file attached to an outgoing chat message.
///
/// The flow mirrors the web UI: bytes are uploaded to
/// ``~/.nexus/vault/uploads/`` (via ``POST /vault/upload``) and the turn is
/// sent referencing the vault path. The chat route resolves each entry into
/// a ``ContentPart`` (image / audio / document).
struct ChatAttachment: Codable, Equatable {
    var vaultPath: String
    var mimeType: String?

    enum CodingKeys: String, CodingKey {
        case vaultPath = "vault_path"
        case mimeType = "mime_type"
    }
}

/// One part of a multipart message (attachment / text segment).
struct ContentPart: Codable, Equatable {
    var kind: String
    var vaultPath: String?
    var text: String?

    enum CodingKeys: String, CodingKey {
        case kind, text
        case vaultPath = "vault_path"
    }
}

/// Message content: plain string, or multipart attachments.
enum MessageContent: Equatable {
    case text(String)
    case parts([ContentPart])

    var displayText: String {
        switch self {
        case .text(let s): return s
        case .parts(let parts):
            return parts.map { $0.text ?? "📎 \($0.vaultPath ?? $0.kind)" }.joined(separator: "\n")
        }
    }
}

struct ChatMessage: Codable, Identifiable, Equatable {
    var seq: Int
    var role: String
    var content: MessageContent
    var toolCalls: [ToolCallInfo]?
    var toolCallId: String?
    var createdAt: String?
    var feedback: Int?
    var pinned: Bool?
    var bot: BotAttribution?

    var id: Int { seq }

    enum CodingKeys: String, CodingKey {
        case seq, role, content, feedback, pinned, bot
        case toolCalls = "tool_calls"
        case toolCallId = "tool_call_id"
        case createdAt = "created_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        seq = Lenient.intValue(c, .seq) ?? 0
        role = try c.decode(String.self, forKey: .role)
        if let s = try? c.decode(String.self, forKey: .content) {
            content = .text(s)
        } else if let raw = try? c.decode([ContentPart].self, forKey: .content) {
            content = .parts(raw)
        } else {
            content = .text("")
        }
        toolCalls = try c.decodeIfPresent([ToolCallInfo].self, forKey: .toolCalls)
        toolCallId = try c.decodeIfPresent(String.self, forKey: .toolCallId)
        createdAt = Lenient.isoString(c, .createdAt)
        feedback = Lenient.intValue(c, .feedback)
        pinned = Lenient.boolValue(c, .pinned)
        bot = try c.decodeIfPresent(BotAttribution.self, forKey: .bot)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(seq, forKey: .seq)
        try c.encode(role, forKey: .role)
        switch content {
        case .text(let s): try c.encode(s, forKey: .content)
        case .parts(let parts):
            try c.encode(parts, forKey: .content)
        }
        try c.encodeIfPresent(toolCalls, forKey: .toolCalls)
        try c.encodeIfPresent(toolCallId, forKey: .toolCallId)
        try c.encodeIfPresent(createdAt, forKey: .createdAt)
        try c.encodeIfPresent(feedback, forKey: .feedback)
        try c.encodeIfPresent(pinned, forKey: .pinned)
        try c.encodeIfPresent(bot, forKey: .bot)
    }

    /// Memberwise initializer (suppressed by the custom Decodable init above).
    init(
        seq: Int,
        role: String,
        content: MessageContent,
        toolCalls: [ToolCallInfo]? = nil,
        toolCallId: String? = nil,
        createdAt: String? = nil,
        feedback: Int? = nil,
        pinned: Bool? = nil,
        bot: BotAttribution? = nil
    ) {
        self.seq = seq
        self.role = role
        self.content = content
        self.toolCalls = toolCalls
        self.toolCallId = toolCallId
        self.createdAt = createdAt
        self.feedback = feedback
        self.pinned = pinned
        self.bot = bot
    }
}

struct ToolCallInfo: Codable, Equatable {
    var id: String?
    var name: String?
    /// The API sends this as a JSON string *or* a raw object — normalize
    /// both to a string.
    var arguments: String?

    enum CodingKeys: String, CodingKey {
        case id, name, arguments
    }

    init(id: String? = nil, name: String? = nil, arguments: String? = nil) {
        self.id = id
        self.name = name
        self.arguments = arguments
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try? c.decode(String.self, forKey: .id)
        name = try? c.decode(String.self, forKey: .name)
        if let s = try? c.decode(String.self, forKey: .arguments) {
            arguments = s
        } else if let obj = try? c.decode(JSONValue.self, forKey: .arguments),
                  let data = try? JSONEncoder().encode(obj),
                  let json = String(data: data, encoding: .utf8) {
            arguments = json
        } else {
            arguments = nil
        }
    }
}

/// "@handle · Name" attribution for messages produced by a mentioned bot.
struct BotAttribution: Codable, Equatable {
    var id: String?
    var name: String?
    var handle: String?
}

struct ChatSession: Codable, Identifiable, Equatable {
    var id: String
    var title: String
    var context: String?
    var projectId: String?
    var team: TeamInfo?
    var messages: [ChatMessage]

    enum CodingKeys: String, CodingKey {
        case id, title, context, team, messages
        case projectId = "project_id"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = (try? c.decode(String.self, forKey: .title)) ?? ""
        context = try? c.decode(String.self, forKey: .context)
        projectId = try? c.decode(String.self, forKey: .projectId)
        team = try? c.decode(TeamInfo.self, forKey: .team)
        // Element-wise decode: one weird message must never blank the
        // whole transcript. Each element is first read as JSONValue
        // (infallible), then retried as a ChatMessage; failures are skipped.
        messages = []
        if let rawList = try? c.decode([JSONValue].self, forKey: .messages) {
            for raw in rawList {
                if let data = try? JSONEncoder().encode(raw),
                   let message = try? JSONDecoder().decode(ChatMessage.self, from: data) {
                    messages.append(message)
                }
            }
        }
    }
}

// MARK: - Chat stream events (POST /chat/stream SSE)

struct StreamDone: Codable, Equatable {
    var sessionId: String?
    var reply: String?
    var iterations: Int?
    var model: String?

    enum CodingKeys: String, CodingKey {
        case reply, iterations, model
        case sessionId = "session_id"
    }
}

struct StreamError: Codable, Equatable {
    var detail: String?
    var reason: String?
    var retryable: Bool?
}

struct QueueEvent: Codable, Equatable {
    var qid: String?
    var text: String?
    var sessionId: String?
    var origin: String?

    enum CodingKeys: String, CodingKey {
        case qid, text, origin
        case sessionId = "session_id"
    }
}

/// One decoded SSE frame from /chat/stream (or /chat/{sid}/turn/stream).
enum ChatStreamEvent: Equatable {
    case delta(String)
    case thinking(String)
    case tool(name: String?, preview: String?)
    case done(StreamDone)
    case error(StreamError)
    case limitReached
    case reconnecting
    case queued(qid: String)
    case userEnqueued(QueueEvent)
    case userInjected(QueueEvent)
    case queueRemoved(qid: String)
    case turnSettled
    case turnBot(name: String?)
    case unknown(event: String)

    static func decode(event: String?, data: String) -> ChatStreamEvent {
        let obj = try? JSONDecoder().decode(JSONValue.self, from: Data(data.utf8))
        switch event ?? "" {
        case "delta":
            return .delta(obj?.stringValue ?? "")
        case "thinking":
            return .thinking(obj?.stringValue ?? "")
        case "tool":
            return .tool(name: obj?["name"]?.stringValue, preview: obj?["result_preview"]?.stringValue)
        case "done":
            var payload = StreamDone(sessionId: nil, reply: nil, iterations: nil, model: nil)
            payload.sessionId = obj?["session_id"]?.stringValue
            payload.reply = obj?["reply"]?.stringValue
            payload.iterations = obj?["iterations"]?.intValue
            payload.model = obj?["model"]?.stringValue
            return .done(payload)
        case "error":
            return .error(StreamError(
                detail: obj?["detail"]?.stringValue,
                reason: obj?["reason"]?.stringValue,
                retryable: obj?["retryable"]?.boolValue
            ))
        case "limit_reached":
            return .limitReached
        case "reconnecting":
            return .reconnecting
        case "queued":
            return .queued(qid: obj?["qid"]?.stringValue ?? "")
        case "user_enqueued":
            return .userEnqueued(decodeQueue(data))
        case "user_injected":
            return .userInjected(decodeQueue(data))
        case "queue_removed":
            return .queueRemoved(qid: obj?["qid"]?.stringValue ?? "")
        case "turn_settled":
            return .turnSettled
        case "turn_bot":
            return .turnBot(name: obj?["name"]?.stringValue ?? obj?["bot"]?.stringValue)
        default:
            return .unknown(event: event ?? "")
        }
    }

    private static func decodeQueue(_ data: String) -> QueueEvent {
        (try? JSONDecoder().decode(QueueEvent.self, from: Data(data.utf8)))
            ?? QueueEvent(qid: nil, text: nil, sessionId: nil, origin: nil)
    }
}

// MARK: - HITL (ask_user)

struct PendingRequest: Codable, Equatable, Identifiable {
    var requestId: String
    var prompt: String
    var kind: String            // confirm | choice | text | form
    var choices: [String]?
    var `default`: String?
    var timeoutSeconds: Double?
    var form: FormSchema?

    var id: String { requestId }

    enum CodingKeys: String, CodingKey {
        case prompt, kind, choices, form
        case requestId = "request_id"
        case `default`
        case timeoutSeconds = "timeout_seconds"
        case formSchema = "form_schema"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        requestId = try c.decode(String.self, forKey: .requestId)
        prompt = try c.decode(String.self, forKey: .prompt)
        kind = try c.decode(String.self, forKey: .kind)
        choices = try c.decodeIfPresent([String].self, forKey: .choices)
        `default` = try c.decodeIfPresent(String.self, forKey: .default)
        timeoutSeconds = try c.decodeIfPresent(Double.self, forKey: .timeoutSeconds)
        // Forms may arrive as `form` or under the pending payload's form_schema key.
        form = try c.decodeIfPresent(FormSchema.self, forKey: .form)
            ?? c.decodeIfPresent(FormSchema.self, forKey: .formSchema)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(requestId, forKey: .requestId)
        try c.encode(prompt, forKey: .prompt)
        try c.encode(kind, forKey: .kind)
        try c.encodeIfPresent(choices, forKey: .choices)
        try c.encodeIfPresent(`default`, forKey: .default)
        try c.encodeIfPresent(timeoutSeconds, forKey: .timeoutSeconds)
        try c.encodeIfPresent(form, forKey: .form)
    }
}

struct FormSchema: Codable, Equatable {
    var title: String?
    var description: String?
    var fields: [FormField]
}

struct FormField: Codable, Equatable, Identifiable {
    var name: String
    var label: String?
    var kind: String?
    var required: Bool?
    var `default`: String?
    var choices: [String]?
    var placeholder: String?
    var help: String?
    var secret: Bool?

    var id: String { name }
}

// MARK: - Browser

struct BrowserInstanceStatus: Codable, Identifiable, Equatable {
    var key: String
    var connected: Bool
    var running: Bool
    var port: Int?
    var url: String?
    var title: String?

    var id: String { key }
}

struct BrowserActionResult: Codable, Equatable {
    var ok: Bool
    var error: String?
    var url: String?
    var title: String?
}

/// Decoded data-only SSE frames from GET /browser/stream.
enum BrowserStreamFrame: Equatable {
    case frame(jpeg: Data)
    case info(url: String?, title: String?, width: Double?, height: Double?)
    case heartbeat
    case error(String)

    static func decode(_ data: String) -> BrowserStreamFrame? {
        guard let obj = try? JSONDecoder().decode(JSONValue.self, from: Data(data.utf8)),
              let type = obj["type"]?.stringValue else { return nil }
        switch type {
        case "frame":
            guard let b64 = obj["data"]?.stringValue, let jpeg = Data(base64Encoded: b64) else { return nil }
            return .frame(jpeg: jpeg)
        case "info":
            return .info(
                url: obj["url"]?.stringValue,
                title: obj["title"]?.stringValue,
                width: obj["width"]?.doubleValue,
                height: obj["height"]?.doubleValue
            )
        case "heartbeat":
            return .heartbeat
        case "error":
            return .error(obj["error"]?.stringValue ?? "stream error")
        default:
            return nil
        }
    }
}

// MARK: - Vault

struct VaultTreeEntry: Codable, Equatable, Identifiable {
    var path: String
    var type: String        // "file" | "dir"
    var size: Int?
    var mtime: Double?

    var id: String { path }
    var name: String { (path as NSString).lastPathComponent }
    var isDirectory: Bool { type == "dir" }
}

struct VaultFile: Codable, Equatable {
    var path: String?
    var content: String?
    var tags: [String]?
}

struct VaultSearchResponse: Codable, Equatable {
    struct Result: Codable, Equatable, Identifiable {
        var path: String?
        var title: String?
        var snippet: String?
        var id: String { path ?? title ?? UUID().uuidString }
    }
    var results: [Result]
    var count: Int?
}

// MARK: - JSON value helper (lenient decoding for stream payloads)

enum JSONValue: Codable, Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case null
    case object([String: JSONValue])
    case array([JSONValue])

    var stringValue: String? {
        if case .string(let s) = self { return s }
        return nil
    }

    var doubleValue: Double? {
        switch self {
        case .number(let d): return d
        case .string(let s): return Double(s)
        default: return nil
        }
    }

    var intValue: Int? { doubleValue.map(Int.init) }
    var boolValue: Bool? {
        if case .bool(let b) = self { return b }
        return nil
    }

    subscript(key: String) -> JSONValue? {
        if case .object(let dict) = self { return dict[key] }
        return nil
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let s = try? c.decode(String.self) { self = .string(s) }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let d = try? c.decode(Double.self) { self = .number(d) }
        else if c.decodeNil() { self = .null }
        else if let dict = try? c.decode([String: JSONValue].self) { self = .object(dict) }
        else if let arr = try? c.decode([JSONValue].self) { self = .array(arr) }
        else { self = .null }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .number(let d): try c.encode(d)
        case .bool(let b): try c.encode(b)
        case .null: try c.encodeNil()
        case .object(let dict): try c.encode(dict)
        case .array(let arr): try c.encode(arr)
        }
    }
}
