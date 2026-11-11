import SwiftUI
import PhotosUI
import UniformTypeIdentifiers
import UIKit

// Attachment staging for the composer. The flow mirrors the web UI: the
// user stages files here, they are uploaded to the vault when the turn is
// sent, and the outgoing message references them by vault path.

/// One media item handed back by the library picker: raw bytes plus the
/// original filename/mime when the provider knows them. `Staging` decides
/// the staging/normalization from here.
struct PickedMedia {
    var name: String?
    var data: Data
}

/// One file staged in the composer, not yet uploaded. Held in memory; the
/// upload happens as part of ``send()`` in ChatView.
struct StagedAttachment: Identifiable {
    let id = UUID()
    var name: String
    var mimeType: String
    var data: Data
    /// JPEG/PNG thumbnail for chips; nil for non-image parts.
    var thumbnail: UIImage?

    var icon: String {
        mimeType.hasPrefix("image/") ? "photo"
            : mimeType.hasPrefix("audio/") ? "waveform"
            : mimeType.hasPrefix("video/") ? "video"
            : "doc"
    }
}

enum AttachmentSource: String {
    case camera = "camera"
    case library = "library"
    case paste = "paste"
    case files = "files"
}

/// Image normalization: fit inside a 1920px box (aspect preserved) and
/// re-encode JPEG 0.85, so camera-sized photos cost a few hundred KB on the
/// Tailscale link instead of tens of MB — matching what the web client
/// asks the Capacitor camera to do natively.
enum ImageOps {
    static let maxEdge: CGFloat = 1920
    static let quality: CGFloat = 0.85

    /// Returns re-encoded JPEG data, or `data` unchanged when it can't be
    /// decoded (e.g. a non-image slipped in) — the server sniffs mimes and
    /// handles documents generically.
    static func normalize(_ data: Data) -> Data {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let image = CGImageSourceCreateThumbnailAtIndex(source, 0, thumbnailOptions as CFDictionary) else {
            return data
        }
        let out = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(out, UTType.jpeg.identifier as CFString, 1, nil) else {
            return data
        }
        CGImageDestinationAddImage(dest, image, jpegProperties as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { return data }
        return out as Data
    }

    private static let thumbnailOptions: [CFString: Any] = [
        kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceShouldCacheImmediately: true,
        kCGImageSourceThumbnailMaxPixelSize: Int(maxEdge),
    ]

    private static let jpegProperties: [CFString: Any] = [
        kCGImageDestinationLossyCompressionQuality: quality,
    ]

    /// Small thumbnail for a composer chip.
    static func chipThumbnail(_ data: Data, maxEdge: CGFloat = 96) -> UIImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let img = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                  kCGImageSourceCreateThumbnailFromImageAlways: true,
                  kCGImageSourceThumbnailMaxPixelSize: Int(maxEdge),
              ] as CFDictionary) else { return nil }
        return UIImage(cgImage: img)
    }
}

enum Staging {
    /// Build a staged attachment: images are normalized (JPEG ≤1920px) with
    /// a chip thumbnail; everything else passes through untouched.
    static func attachment(name rawName: String, data: Data) -> StagedAttachment {
        let type = UTType(filenameExtension: (rawName as NSString).pathExtension) ?? .data
        if type.conforms(to: .image) {
            let normalized = ImageOps.normalize(data)
            let base = rawName.isEmpty || !rawName.contains(".") ? "photo" : (rawName as NSString).deletingPathExtension
            return StagedAttachment(
                name: base.isEmpty ? "photo.jpg" : "\(base).jpg",
                mimeType: "image/jpeg",
                data: normalized,
                thumbnail: ImageOps.chipThumbnail(normalized)
            )
        }
        return StagedAttachment(
            name: rawName.isEmpty ? "file" : rawName,
            mimeType: type.preferredMIMEType ?? "application/octet-stream",
            data: data,
            thumbnail: nil
        )
    }

    /// Timestamped photo name matching the web client's convention.
    static func photoName() -> String {
        let stamp = ISO8601DateFormatter().string(from: Date())
            .replacingOccurrences(of: ":", with: "-")
            .replacingOccurrences(of: "+", with: "z")
        return "photo-\(stamp).jpg"
    }

    /// Server-side classification of the attachment mime (image / audio /
    /// document) — used for optimistic display only; the chat route
    /// re-classifies on its own.
    static func contentKind(mime: String) -> String {
        mime.hasPrefix("image/") ? "image"
            : mime.hasPrefix("audio/") ? "audio"
            : "document"
    }
}

/// Shared NSItemProvider → PickedMedia loader for pasteboard and picker
/// providers. Consumes the first media UTI each provider offers; fileURL-
/// only providers (Files-app Copy of arbitrary typeless files) are left
/// out by the callers' filters.
enum PasteboardLoader {
    static func load(_ providers: [NSItemProvider]) async -> [PickedMedia] {
        var picked: [PickedMedia] = []
        for provider in providers {
            let preferred: String
            let isMovie = provider.hasItemConformingToTypeIdentifier(UTType.movie.identifier)
            let isImage = provider.hasItemConformingToTypeIdentifier(UTType.image.identifier)
            if isMovie { preferred = UTType.movie.identifier }
            else if isImage { preferred = UTType.image.identifier }
            else { continue }
            if let data = try? await loadProviderData(provider, uti: preferred) {
                if data.count <= PhotoLibraryPicker.maxBytes {
                    picked.append(PickedMedia(name: provider.suggestedName, data: data))
                }
            }
        }
        return picked
    }

    static func loadProviderData(
        _ provider: NSItemProvider,
        uti: String
    ) async throws -> Data {
        try await withCheckedThrowingContinuation { cont in
            _ = provider.loadDataRepresentation(forTypeIdentifier: uti) { data, err in
                if let data {
                    cont.resume(returning: data)
                } else {
                    cont.resume(
                        throwing: err ?? NSError(
                            domain: "PasteboardLoader", code: 1,
                            userInfo: [NSLocalizedDescriptionKey: "No media data"]
                        )
                    )
                }
            }
        }
    }
}

/// PHPickerViewController wrapper for the photo library (multiple images).
/// Used instead of SwiftUI's PhotosPicker so the library can live inside
/// the attach Menu — PhotosPicker refuses to present from a menu item.
struct PhotoLibraryPicker: UIViewControllerRepresentable {
    var limit = 8
    var onPicked: ([PickedMedia]) -> Void
    var onOversized: (String) -> Void = { _ in }
    var onCancel: () -> Void

    /// Memory ceiling for a single library pick (~5 min of 1080p).
    nonisolated static let maxBytes = 300 * 1024 * 1024

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIViewController(context: Context) -> PHPickerViewController {
        var config = PHPickerConfiguration()
        config.selectionLimit = limit
        // Photos *and* videos — a library filter of `.images` alone made
        // filmed clips invisible in the picker.
        config.filter = .any(of: [.images, .videos])
        let picker = PHPickerViewController(configuration: config)
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ uiViewController: PHPickerViewController, context: Context) {}

    final class Coordinator: NSObject, PHPickerViewControllerDelegate {
        let parent: PhotoLibraryPicker
        init(_ parent: PhotoLibraryPicker) { self.parent = parent }

        func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
            let providers = results.map(\.itemProvider)
            guard !providers.isEmpty else {
                parent.onCancel()
                return
            }
            let parent = self.parent
            Task { @MainActor in
                var picked: [PickedMedia] = []
                // Ordered as the user selected them. Movies load from
                // their own UTI — images and videos are registered
                // under different type identifiers.
                for provider in providers {
                    let isMovie = provider.hasItemConformingToTypeIdentifier(UTType.movie.identifier)
                    let uti = isMovie ? UTType.movie.identifier : UTType.image.identifier
                    if let data = try? await PasteboardLoader.loadProviderData(provider, uti: uti) {
                        // Memory guard: a multi-GB 4K film staged as Data
                        // will jetsam the app — skip oversized picks and
                        // let the caller surface a message.
                        if data.count > PhotoLibraryPicker.maxBytes {
                            parent.onOversized(provider.suggestedName ?? "media")
                            continue
                        }
                        picked.append(PickedMedia(name: provider.suggestedName, data: data))
                    }
                }
                parent.onPicked(picked)
            }
        }
    }
}

/// UIImagePickerController wrapper for the camera source — SwiftUI's
/// PhotosPicker can't capture.
struct CameraPicker: UIViewControllerRepresentable {
    var onPicked: (Data) -> Void
    var onCancel: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = UIImagePickerController.isSourceTypeAvailable(.camera) ? .camera : .photoLibrary
        picker.mediaTypes = ["public.image"]
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ uiViewController: UIImagePickerController, context: Context) {}

    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let parent: CameraPicker
        init(_ parent: CameraPicker) { self.parent = parent }

        func imagePickerController(
            _ picker: UIImagePickerController,
            didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]
        ) {
            if let image = info[.originalImage] as? UIImage,
               let data = image.jpegData(compressionQuality: 1.0) {
                parent.onPicked(data)
            } else {
                parent.onCancel()
            }
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            parent.onCancel()
        }
    }
}
