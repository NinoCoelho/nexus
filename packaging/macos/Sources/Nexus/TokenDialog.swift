import AppKit

@MainActor
enum TokenDialog {
    static func showCurrentToken() {
        let token = readToken()
        let alert = NSAlert()
        alert.messageText = "Nexus Access Token"
        if token.isEmpty {
            alert.informativeText = "No access token has been generated yet — start the server at least once."
            alert.addButton(withTitle: "OK")
            NSApp.activate(ignoringOtherApps: true)
            alert.runModal()
            return
        }
        alert.informativeText = """
        Use this token in the Authorization header when accessing Nexus from another machine:

        Authorization: Bearer \(token)

        Loopback requests (this Mac) do not require it.
        """
        alert.addButton(withTitle: "Copy to Clipboard")
        alert.addButton(withTitle: "Close")
        NSApp.activate(ignoringOtherApps: true)
        let response = alert.runModal()
        if response == .alertFirstButtonReturn {
            let pb = NSPasteboard.general
            pb.clearContents()
            pb.setString(token, forType: .string)
        }
    }

    private static func readToken() -> String {
        let url = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".nexus/access_token")
        return (try? String(contentsOf: url, encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }
}
