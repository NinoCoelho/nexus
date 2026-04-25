import AppKit
import WebKit

/// Hosts a WKWebView pointing at the local FastAPI server.
@MainActor
final class WebWindowController: NSObject, NSWindowDelegate {
    private let window: NSWindow
    private let webView: WKWebView
    private var currentURL: URL

    init(url: URL) {
        self.currentURL = url

        let config = WKWebViewConfiguration()
        config.preferences.javaScriptCanOpenWindowsAutomatically = true
        config.websiteDataStore = .default()
        let prefs = WKWebpagePreferences()
        prefs.allowsContentJavaScript = true
        config.defaultWebpagePreferences = prefs

        let frame = NSRect(x: 0, y: 0, width: 1280, height: 820)
        self.webView = WKWebView(frame: frame, configuration: config)
        self.webView.autoresizingMask = [.width, .height]

        let style: NSWindow.StyleMask = [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView]
        self.window = NSWindow(contentRect: frame, styleMask: style, backing: .buffered, defer: false)
        self.window.title = "Nexus"
        self.window.contentView = webView
        self.window.center()
        self.window.setFrameAutosaveName("NexusMainWindow")
        self.window.isReleasedWhenClosed = false

        super.init()
        self.window.delegate = self
        self.webView.load(URLRequest(url: url))
    }

    func show() {
        NSApp.activate(ignoringOtherApps: true)
        if !window.isVisible {
            webView.load(URLRequest(url: currentURL))
        }
        window.makeKeyAndOrderFront(nil)
    }

    func reload(url: URL) {
        currentURL = url
        webView.load(URLRequest(url: url))
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        // Hide instead of destroy — server keeps running, menu bar reopens it.
        sender.orderOut(nil)
        return false
    }
}
