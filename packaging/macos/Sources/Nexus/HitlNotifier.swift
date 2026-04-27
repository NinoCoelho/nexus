import Foundation
import UserNotifications
import AppKit

/// Subscribes to the daemon's `/notifications/events` SSE stream and posts
/// native macOS notifications for HITL prompts. Click on a notification opens
/// the user's default browser at a deeplink so the right session + dialog is
/// rendered.
///
/// Inline actions (Approve / Deny) are wired for `kind="confirm"` so a quick
/// yes/no doesn't need a browser round-trip. Forms / choices / text always
/// require opening the browser because the answer is structured.
///
/// Reconnect-with-backoff loop survives daemon restarts (Restart Server,
/// Preferences apply): the SSE socket breaks, we wait + reopen.
@MainActor
final class HitlNotifier: NSObject {
    private weak var server: ServerController?
    private var sseTask: Task<Void, Never>?
    private let center = UNUserNotificationCenter.current()

    /// Identifiers for the inline actions wired to confirm-kind prompts.
    /// Titles are user-visible; identifiers are what the delegate sees.
    private static let confirmCategoryId = "nexus.confirm"
    private static let approveActionId = "nexus.approve"
    private static let denyActionId = "nexus.deny"

    /// Begin listening. Safe to call after every server (re)start.
    func start(server: ServerController) {
        self.server = server
        center.delegate = self
        registerCategories()
        Task { await self.requestAuthorizationOnce() }
        sseTask?.cancel()
        sseTask = Task { [weak self] in await self?.subscribeLoop() }
    }

    /// Cancel the SSE loop. Used when the server is being restarted or the
    /// app is quitting. Does NOT clear delivered notifications — those stay
    /// in Notification Center until the user dismisses or actions them.
    func stop() {
        sseTask?.cancel()
        sseTask = nil
    }

    // ── Authorization ───────────────────────────────────────────────────

    /// Ask once at startup. macOS persists the answer; subsequent calls are
    /// no-ops. Errors are logged but not surfaced — a denial just means the
    /// user won't see banners, the app keeps working.
    private func requestAuthorizationOnce() async {
        do {
            _ = try await center.requestAuthorization(
                options: [.alert, .sound, .badge]
            )
        } catch {
            NSLog("[Nexus] notification authorization failed: \(error)")
        }
    }

    // ── Categories with inline actions ──────────────────────────────────

    private func registerCategories() {
        let approve = UNNotificationAction(
            identifier: Self.approveActionId,
            title: "Approve",
            options: [.foreground]
        )
        let deny = UNNotificationAction(
            identifier: Self.denyActionId,
            title: "Deny",
            options: [.destructive]
        )
        let confirmCategory = UNNotificationCategory(
            identifier: Self.confirmCategoryId,
            actions: [approve, deny],
            intentIdentifiers: [],
            options: []
        )
        center.setNotificationCategories([confirmCategory])
    }

    // ── SSE subscription ────────────────────────────────────────────────

    /// Reconnect-with-backoff loop. The daemon's SSE endpoint stays open
    /// indefinitely; if the connection breaks (server restart, network
    /// blip), we just retry with exponential backoff capped at 10 s.
    private func subscribeLoop() async {
        var backoffNs: UInt64 = 1_000_000_000  // 1 s, doubles up to 10 s
        while !Task.isCancelled {
            do {
                try await self.subscribeOnce()
                backoffNs = 1_000_000_000  // reset after a clean run
            } catch is CancellationError {
                return
            } catch {
                NSLog("[Nexus] SSE error: \(error.localizedDescription)")
            }
            try? await Task.sleep(nanoseconds: backoffNs)
            backoffNs = min(backoffNs * 2, 10_000_000_000)
        }
    }

    private func subscribeOnce() async throws {
        guard let port = server?.port else {
            throw URLError(.badURL)
        }
        guard let url = URL(string: "http://127.0.0.1:\(port)/notifications/events") else {
            throw URLError(.badURL)
        }

        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        req.timeoutInterval = .infinity
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        // Loopback gets a free pass on the auth middleware, but include the
        // token defensively in case that policy ever changes.
        if let token = Self.readToken() {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        let (bytes, response) = try await URLSession.shared.bytes(for: req)
        if let http = response as? HTTPURLResponse, http.statusCode != 200 {
            throw URLError(.badServerResponse)
        }

        // Minimal SSE parser: blank line separates events, "event: <kind>"
        // sets the next "data: <json>"'s kind, "data: <json>" carries the
        // payload. We ignore comments (": ...") used as keepalives.
        var currentEvent: String? = nil
        for try await line in bytes.lines {
            if line.isEmpty {
                currentEvent = nil
                continue
            }
            if line.hasPrefix(":") { continue }
            if line.hasPrefix("event: ") {
                currentEvent = String(line.dropFirst("event: ".count))
                continue
            }
            if line.hasPrefix("data: "), let kind = currentEvent {
                let json = String(line.dropFirst("data: ".count))
                guard let data = json.data(using: .utf8),
                      let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
                else { continue }
                self.handle(kind: kind, data: dict)
            }
        }
    }

    // ── Event dispatch ──────────────────────────────────────────────────

    private func handle(kind: String, data: [String: Any]) {
        switch kind {
        case "user_request":
            schedule(data: data)
        case "user_request_cancelled", "user_request_auto":
            // Either the daemon timed it out / cancelled it, or YOLO mode
            // auto-answered before the user could. Either way, kill any
            // banner we already posted so the Notification Center matches
            // the bell's history.
            if let rid = data["request_id"] as? String {
                center.removeDeliveredNotifications(withIdentifiers: [rid])
                center.removePendingNotificationRequests(withIdentifiers: [rid])
            }
        default:
            break
        }
    }

    private func schedule(data: [String: Any]) {
        let prompt = (data["prompt"] as? String) ?? "Approval needed"
        let kind = (data["kind"] as? String) ?? "confirm"
        let rid = (data["request_id"] as? String) ?? UUID().uuidString
        let sid = (data["session_id"] as? String) ?? ""

        let content = UNMutableNotificationContent()
        content.title = "Nexus"
        switch kind {
        case "confirm":
            content.subtitle = "Approval needed"
            content.categoryIdentifier = Self.confirmCategoryId
        case "form":
            content.subtitle = data["form_title"] as? String ?? "Form requested"
        case "choice":
            content.subtitle = "Choose an option"
        case "text":
            content.subtitle = "Input requested"
        default:
            content.subtitle = "Approval needed"
        }
        content.body = prompt
        content.sound = .default
        content.userInfo = [
            "request_id": rid,
            "session_id": sid,
            "kind": kind,
        ]
        // Use the request_id as the notification identifier so cancellations
        // can target it precisely (see handle(kind:data:) above).
        let req = UNNotificationRequest(
            identifier: rid, content: content, trigger: nil
        )
        center.add(req) { error in
            if let error = error {
                NSLog("[Nexus] notification add failed: \(error)")
            }
        }
    }

    // ── Helpers ─────────────────────────────────────────────────────────

    /// Read the persistent access token written by `bootstrap.py` to
    /// `~/.nexus/access_token`. Used in the Authorization header so the
    /// SSE subscription works even if the loopback exemption is ever
    /// tightened.
    static func readToken() -> String? {
        let url = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".nexus/access_token")
        guard let s = try? String(contentsOf: url, encoding: .utf8) else {
            return nil
        }
        let trimmed = s.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    fileprivate func openInBrowser(sessionId: String, requestId: String) {
        guard let port = server?.port else { return }
        // Always 127.0.0.1 in the browser even if the daemon is bound to
        // 0.0.0.0 — opening 0.0.0.0 doesn't work in browsers, and loopback
        // is exempt from the auth check. Mirrors AppController.openInBrowser.
        let urlStr = "http://127.0.0.1:\(port)/?session=\(sessionId)&request_id=\(requestId)"
        guard let url = URL(string: urlStr) else { return }
        NSWorkspace.shared.open(url)
    }

    fileprivate func postRespond(
        sessionId: String, requestId: String, answer: String
    ) async {
        guard let port = server?.port,
              let url = URL(string: "http://127.0.0.1:\(port)/chat/\(sessionId)/respond")
        else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token = Self.readToken() {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let body: [String: Any] = ["request_id": requestId, "answer": answer]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        do {
            let (_, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse, http.statusCode == 409 {
                // 409 = parked. The daemon ended the turn waiting; the
                // /respond shortcut isn't valid. Open the browser so the
                // user can answer via the resume flow.
                await MainActor.run {
                    self.openInBrowser(sessionId: sessionId, requestId: requestId)
                }
            }
        } catch {
            NSLog("[Nexus] /respond failed: \(error)")
        }
    }
}

extension HitlNotifier: UNUserNotificationCenterDelegate {
    /// Show banners + sound even if our app happens to be the frontmost
    /// process (default behaviour suppresses in-app notifications).
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound, .list])
    }

    /// Click + inline-action handler.
    ///   * Approve / Deny on a confirm → POST /respond directly.
    ///   * Anything else (default click, form / text / choice) → open the
    ///     browser at the deeplink so the dialog renders.
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        let info = response.notification.request.content.userInfo
        let rid = (info["request_id"] as? String) ?? ""
        let sid = (info["session_id"] as? String) ?? ""
        let kind = (info["kind"] as? String) ?? "confirm"
        let action = response.actionIdentifier

        Task { @MainActor [weak self] in
            switch action {
            case Self.approveActionId where kind == "confirm":
                await self?.postRespond(sessionId: sid, requestId: rid, answer: "yes")
            case Self.denyActionId where kind == "confirm":
                await self?.postRespond(sessionId: sid, requestId: rid, answer: "no")
            default:
                self?.openInBrowser(sessionId: sid, requestId: rid)
            }
            completionHandler()
        }
    }
}
