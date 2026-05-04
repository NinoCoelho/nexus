import AppKit
import WebKit

@MainActor
final class MainWindowController: NSObject, NSWindowDelegate {
    private var window: NSWindow?
    private var webView: WKWebView?

    func show(url: URL) {
        if let w = window {
            NSApp.activate(ignoringOtherApps: true)
            w.makeKeyAndOrderFront(nil)
            if let url = webView?.url, url != url {
                webView?.load(URLRequest(url: url))
            }
            return
        }

        let config = WKWebViewConfiguration()
        config.preferences.isElementFullscreenEnabled = true
        let wv = WKWebView(frame: .zero, configuration: config)
        wv.load(URLRequest(url: url))
        self.webView = wv

        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 800),
                         styleMask: [.titled, .closable, .miniaturizable, .resizable],
                         backing: .buffered,
                         defer: false)
        w.title = "Nexus"
        w.contentView = wv
        w.delegate = self
        w.isReleasedWhenClosed = false
        w.center()
        self.window = w

        NSApp.activate(ignoringOtherApps: true)
        w.makeKeyAndOrderFront(nil)
    }

    func load(url: URL) {
        webView?.load(URLRequest(url: url))
        if let w = window, w.isVisible == false {
            NSApp.activate(ignoringOtherApps: true)
            w.makeKeyAndOrderFront(nil)
        }
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        sender.orderOut(nil)
        return false
    }
}
