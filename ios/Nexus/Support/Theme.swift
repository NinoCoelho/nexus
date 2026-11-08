import SwiftUI

/// Design tokens — mirrors the web UI's dark-first palette.
enum Theme {
    static let accent = Color(red: 0.545, green: 0.404, blue: 0.984)

    static var background: Color {
        Color(uiColor: .systemGroupedBackground)
    }

    static var panel: Color {
        Color(uiColor: .secondarySystemGroupedBackground)
    }

    static var bubbleUser: Color {
        accent.opacity(0.28)
    }

    static var bubbleBot: Color {
        Color(uiColor: .secondarySystemGroupedBackground)
    }

    static var subtleText: Color {
        .secondary
    }
}

struct Badge: View {
    let text: String
    var body: some View {
        Text(text)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(Theme.accent.opacity(0.18), in: Capsule())
            .foregroundStyle(Theme.accent)
    }
}

struct ErrorBanner: View {
    let message: String
    var body: some View {
        Label(message, systemImage: "wifi.exclamationmark")
            .font(.footnote)
            .foregroundStyle(.red)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
    }
}
