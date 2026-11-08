import Foundation

/// A parsed Server-Sent-Events frame.
struct SSEEvent: Equatable {
    /// `event:` field — nil for data-only streams (browser live view).
    var event: String?
    /// Joined `data:` lines.
    var data: String
}

enum SSEError: Error {
    case badResponse(status: Int, body: String)
    case invalidUTF8
}

/// Incremental SSE parser: feed it raw lines (without trailing newlines),
/// get complete events out. Tolerates `: keepalive` comments, CRLF, and
/// multi-line data fields.
final class SSEParser {
    private var dataLines: [String] = []
    private var eventName: String?

    /// Feed one line; returns an event when a frame boundary (blank line) completes it.
    func feed(_ rawLine: String) -> SSEEvent? {
        // Strip a single trailing \r (CRLF transports).
        var line = rawLine
        if line.hasSuffix("\r") { line.removeLast() }

        if line.isEmpty {
            // Frame boundary — emit if we have data.
            defer { dataLines = []; eventName = nil }
            guard !dataLines.isEmpty else { return nil }
            return SSEEvent(event: eventName, data: dataLines.joined(separator: "\n"))
        }
        if line.hasPrefix(":") {
            return nil // comment / keepalive
        }
        if line.hasPrefix("event:") {
            eventName = String(line.dropFirst(6)).trimmingCharacters(in: .whitespaces)
        } else if line.hasPrefix("data:") {
            var payload = String(line.dropFirst(5))
            if payload.hasPrefix(" ") { payload.removeFirst() }
            dataLines.append(payload)
        }
        // Other fields (id:, retry:) are ignored.
        return nil
    }

    /// Flush any buffered partial frame (some servers end the stream mid-frame).
    func flush() -> SSEEvent? {
        defer { dataLines = []; eventName = nil }
        guard !dataLines.isEmpty else { return nil }
        return SSEEvent(event: eventName, data: dataLines.joined(separator: "\n"))
    }
}

extension URLSession {
    /// Stream SSE events from a request. The AsyncThrowingStream ends when
    /// the server closes the connection (normal for SSE) — callers decide
    /// whether to reconnect.
    ///
    /// Some servers (notably FastAPI under proxies) emit bare-JSON guard
    /// lines without `data:` prefixes; those are surfaced as events with
    /// `data` set to the raw line and `event == nil` — the Chrome panel's
    /// readSse applies the same tolerance.
    func sseEvents(for request: URLRequest) -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                let (bytes, response): (URLSession.AsyncBytes, URLResponse)
                do {
                    (bytes, response) = try await self.bytes(for: request)
                } catch {
                    continuation.finish(throwing: error)
                    return
                }
                if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
                    var body = ""
                    if let raw = try? await bytes.lines.reduce(into: "", { $0 += "\($1)" }) {
                        body = String(raw.prefix(500))
                    }
                    continuation.finish(throwing: SSEError.badResponse(status: http.statusCode, body: body))
                    return
                }
                let parser = SSEParser()
                do {
                    for try await rawLine in bytes.lines {
                        if Task.isCancelled { break }
                        if let starts = parser.feed(rawLine) {
                            continuation.yield(starts)
                        } else if rawLine.hasPrefix("{") && !rawLine.hasPrefix("data:") {
                            // Bare-JSON guard line (oversize/context-overflow paths).
                            continuation.yield(SSEEvent(event: nil, data: rawLine))
                        }
                    }
                    if let rest = parser.flush() {
                        continuation.yield(rest)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in
                task.cancel()
            }
        }
    }
}
