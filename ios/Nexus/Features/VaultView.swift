import SwiftUI

/// Read-only vault: folder drill-down, markdown reader, full-text search.
struct VaultView: View {
    @Environment(ServerStore.self) private var servers

    @State private var search = ""
    @State private var results: [VaultSearchResponse.Result] = []
    @State private var searching = false

    var body: some View {
        NavigationStack {
            VaultFolderView(prefix: "")
                .searchable(text: $search, prompt: "Search the vault")
                .onChange(of: search) { _, newValue in
                    let query = newValue.trimmingCharacters(in: .whitespaces)
                    guard query.count >= 2 else {
                        results = []
                        return
                    }
                    Task { await runSearch(query) }
                }
                .overlay(alignment: .top) {
                    if !results.isEmpty {
                        ScrollView {
                            VStack(alignment: .leading, spacing: 8) {
                                ForEach(results) { result in
                                    NavigationLink {
                                        VaultFileView(path: result.path ?? "")
                                    } label: {
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(result.title ?? result.path ?? "")
                                                .font(.body)
                                                .lineLimit(1)
                                            if let snippet = result.snippet {
                                                Text(snippet)
                                                    .font(.caption)
                                                    .foregroundStyle(Theme.subtleText)
                                                    .lineLimit(2)
                                            }
                                        }
                                    }
                                    .buttonStyle(.plain)
                                }
                            }
                            .padding()
                        }
                        .background(.thinMaterial)
                        .frame(maxHeight: 340)
                        .padding(.horizontal, 8)
                        .padding(.top, 4)
                        .transition(.opacity)
                    }
                }
        }
    }

    private func runSearch(_ query: String) async {
        guard let client = servers.client else { return }
        searching = true
        defer { searching = false }
        if let response = try? await client.vaultSearch(query) {
            results = response.results
        }
    }
}

/// One folder level, built from the flat /vault/tree listing.
struct VaultFolderView: View {
    let prefix: String

    @Environment(ServerStore.self) private var servers
    @State private var entries: [VaultTreeEntry] = []
    @State private var error: String?
    @State private var loading = true

    private var folders: [VaultTreeEntry] { entries.filter(\.isDirectory).sorted { $0.name < $1.name } }
    private var files: [VaultTreeEntry] { entries.filter { !$0.isDirectory }.sorted { $0.name < $1.name } }

    var body: some View {
        List {
            if let error {
                Section { ErrorBanner(message: error) }
            }
            if !folders.isEmpty {
                Section {
                    ForEach(folders) { folder in
                        NavigationLink {
                            VaultFolderView(prefix: folder.path)
                        } label: {
                            Label(folder.name, systemImage: "folder.fill")
                        }
                    }
                }
            }
            Section {
                if loading && entries.isEmpty {
                    ProgressView()
                } else if files.isEmpty && folders.isEmpty {
                    Text("Empty")
                        .foregroundStyle(Theme.subtleText)
                }
                ForEach(files) { file in
                    NavigationLink {
                        VaultFileView(path: file.path)
                    } label: {
                        Label(file.name, systemImage: "doc.text")
                    }
                }
            } header: {
                if !folders.isEmpty { Text("Files") }
            }
        }
        .navigationTitle(prefix.isEmpty ? "Vault" : (prefix as NSString).lastPathComponent)
        .navigationBarTitleDisplayMode(prefix.isEmpty ? .large : .inline)
        .task { await load() }
    }

    private func load() async {
        guard let client = servers.client else { return }
        do {
            let tree = try await client.vaultTree()
            // Children of this folder only (paths are relative with "/" separators).
            let prefixPath = prefix.isEmpty ? "" : prefix + "/"
            entries = tree.filter { entry in
                guard entry.path.hasPrefix(prefixPath), entry.path != prefix else { return false }
                let rest = String(entry.path.dropFirst(prefixPath.count))
                return !rest.isEmpty
            }
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        loading = false
    }
}

struct VaultFileView: View {
    let path: String

    @Environment(ServerStore.self) private var servers
    @State private var content: String?
    @State private var error: String?

    var body: some View {
        ScrollView {
            if let content {
                MarkdownText(markdown: content)
                    .padding()
            } else if let error {
                ErrorBanner(message: error).padding()
            } else {
                ProgressView().padding(.top, 60)
            }
        }
        .navigationTitle((path as NSString).lastPathComponent)
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
    }

    private func load() async {
        guard let client = servers.client else { return }
        do {
            let file = try await client.vaultFile(path: path)
            content = file.content ?? "(empty file)"
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}
