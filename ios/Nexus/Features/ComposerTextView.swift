import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// UITextView-based composer line, replacing the stock SwiftUI TextField.
///
/// Why: the system Paste callout on a plain TextField silently drops
/// non-text clipboard contents. Here the paste gesture is intercepted:
/// images (screenshot), videos and files (Copy in the Files app) on the
/// clipboard are routed to attachment staging, while text-only paste keeps
/// the stock insertion. Matching the previous TextField contract, the
/// return key submits instead of inserting a newline (mobile keyboards
/// don't offer a newline-with-modifier; the send path is unchanged).
struct ComposerTextView: UIViewRepresentable {

    @Binding var text: String
    var placeholder: String
    var disabled: Bool
    /// Paste carried a non-text item — stage it as an attachment.
    var onPasteAttachment: () -> Void
    var onSubmit: () -> Void
    /// Current ideal height, pushed up so SwiftUI can size the frame.
    var onHeightChange: (CGFloat) -> Void

    static let maxLinesHeight: CGFloat = 128

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> AttachPasteTextView {
        let tv = AttachPasteTextView()
        tv.font = .systemFont(ofSize: 17)
        tv.backgroundColor = .clear
        tv.isScrollEnabled = false
        tv.textContainerInset = .zero
        tv.textContainer.lineFragmentPadding = 0
        tv.delegate = context.coordinator
        tv.onPasteAttachment = { context.coordinator.parent.onPasteAttachment() }

        let label = UILabel()
        label.text = placeholder
        label.font = .systemFont(ofSize: 17)
        label.textColor = UIColor.placeholderText
        label.translatesAutoresizingMaskIntoConstraints = false
        label.tag = ComposerTextView.placeholderTag
        tv.addSubview(label)
        NSLayoutConstraint.activate([
            label.topAnchor.constraint(equalTo: tv.topAnchor, constant: 2),
            label.leadingAnchor.constraint(equalTo: tv.leadingAnchor),
        ])
        return tv
    }

    func updateUIView(_ tv: AttachPasteTextView, context: Context) {
        context.coordinator.parent = self
        // Programmatic clears (send resets the draft) must reflect; echo
        // updates from typing are no-ops because the strings already match.
        if tv.text != text {
            tv.text = text
            context.coordinator.syncPlaceholder(tv)
            context.coordinator.fitHeight(tv)
        }
        tv.isUserInteractionEnabled = !disabled
        tv.isEditable = !disabled
        tv.alpha = disabled ? 0.5 : 1
    }

    static let placeholderTag = 0x4E545811  // "NTX."

    final class Coordinator: NSObject, UITextViewDelegate {
        var parent: ComposerTextView

        init(_ parent: ComposerTextView) { self.parent = parent }

        func textViewDidChange(_ tv: UITextView) {
            parent.text = tv.text ?? ""
            syncPlaceholder(tv)
            fitHeight(tv as! AttachPasteTextView)
        }

        func textView(
            _ tv: UITextView,
            shouldChangeTextIn range: NSRange,
            replacementText txt: String
        ) -> Bool {
            if txt == "\n" {
                parent.onSubmit()
                return false
            }
            return true
        }

        func syncPlaceholder(_ tv: UITextView) {
            let label = tv.viewWithTag(ComposerTextView.placeholderTag) as? UILabel
            label?.isHidden = !tv.text.isEmpty
        }

        /// Grow with content up to maxLinesHeight, then scroll internally.
        func fitHeight(_ tv: UITextView) {
            let fit = tv.sizeThatFits(
                CGSize(width: tv.bounds.width, height: .greatestFiniteMagnitude)
            ).height
            let target = min(max(fit, 22), ComposerTextView.maxLinesHeight)
            tv.isScrollEnabled = fit > target + 1
            parent.onHeightChange(target)
        }
    }
}

/// The paste-aware text view: non-text clips on the pasteboard trigger a
/// callback instead of the default insertion no-op.
final class AttachPasteTextView: UITextView {
    var onPasteAttachment: (() -> Void)?

    /// Files copied in the Files app show up as pasteboard item providers
    /// (e.g. a .mov registered under public.movie).
    private var fileProviders: [NSItemProvider] {
        UIPasteboard.general.itemProviders.filter {
            $0.hasItemConformingToTypeIdentifier(UTType.movie.identifier) ||
            $0.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier)
        }
    }

    private var clipboardHasAttachment: Bool {
        UIPasteboard.general.hasImages || !fileProviders.isEmpty
    }

    override func canPerformAction(_ action: Selector, withSender sender: Any?) -> Bool {
        if action == #selector(paste(_:)) {
            // Make Paste selectable even when the clipboard holds only a
            // media file (stock logic only checks text). `hasStrings` reads
            // pasteboard metadata only — touching `.string` here would trip
            // the system paste-permission prompt on every long-press,
            // before the user pastes anything.
            return UIPasteboard.general.hasStrings || clipboardHasAttachment
        }
        return super.canPerformAction(action, withSender: sender)
    }

    override func paste(_ sender: Any?) {
        if clipboardHasAttachment {
            onPasteAttachment?()
            return
        }
        super.paste(sender)
    }
}
