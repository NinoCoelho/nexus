import SwiftUI
import UserNotifications

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task(priority: .utility) {
            guard let client = ServerStore.shared.client else { return }
            try? await client.registerApns(token: token)
        }
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        print("APNs registration failed: \(error.localizedDescription)")
    }

    // Show HITL pushes as banners even while the app is foregrounded.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        // Deep link: open the app on the session the prompt belongs to.
        if let sid = response.notification.request.content.userInfo["session_id"] as? String {
            await MainActor.run {
                NotificationCenter.default.post(
                    name: .openSession, object: nil, userInfo: ["session_id": sid]
                )
            }
        }
    }
}

extension Notification.Name {
    static let openSession = Notification.Name("nexus.openSession")
}

/// Bumped on every device install so the running build is identifiable
/// on-screen (Settings → About, and the pairing empty state).
enum BuildInfo {
    static let stamp = "B10-2415"
}

@main
struct NexusApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @State private var servers = ServerStore.shared
    @State private var pendingPairURL: URL?

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(servers)
                .onOpenURL { url in
                    if let profile = ServerProfile.fromDeepLink(url) {
                        pendingPairURL = url
                        servers.lastError = nil
                        // RootView presents PairingConfirmView for this URL.
                        NotificationCenter.default.post(
                            name: .pairDeepLink, object: nil,
                            userInfo: ["profile": profile]
                        )
                    }
                }
                .task {
                    await requestPushIfNeeded()
                }
        }
    }

    private func requestPushIfNeeded() async {
        let center = UNUserNotificationCenter.current()
        guard let granted = try? await center.requestAuthorization(options: [.alert, .sound, .badge]), granted else { return }
        await MainActor.run {
            UIApplication.shared.registerForRemoteNotifications()
        }
    }
}

extension Notification.Name {
    static let pairDeepLink = Notification.Name("nexus.pairDeepLink")
}

