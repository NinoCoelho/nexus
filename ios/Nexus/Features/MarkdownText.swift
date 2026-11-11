import SwiftUI

/// Lightweight markdown renderer for chat bubbles and vault files.
/// Block-level structure is parsed here; inline emphasis/links/code go
/// through `AttributedString(markdown:)`. No external dependencies.
struct MarkdownText: View {
    let markdown: String
    /// Chat-search term to highlight (case/diacritic-insensitive), if any.
    var highlight: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(parse(markdown).enumerated()), id: \.offset) { _, block in
                blockView(block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Block model

    enum Block {
        case paragraph(String)
        case heading(level: Int, text: String)
        case code(lang: String?, body: String)
        case bullet(items: [String])
        case ordered(items: [String])
        case quote(String)
        case divider
    }

    func parse(_ source: String) -> [Block] {
        var blocks: [Block] = []
        var paragraph: [String] = []
        var bullets: [String] = []
        var ordered: [String] = []
        var codeLines: [String]?
        var codeLang: String?
        var quote: [String] = []

        func flushParagraph() {
            if !paragraph.isEmpty {
                blocks.append(.paragraph(paragraph.joined(separator: " ")))
                paragraph = []
            }
        }
        func flushBullets() {
            if !bullets.isEmpty {
                blocks.append(.bullet(items: bullets))
                bullets = []
            }
        }
        func flushOrdered() {
            if !ordered.isEmpty {
                blocks.append(.ordered(items: ordered))
                ordered = []
            }
        }
        func flushQuote() {
            if !quote.isEmpty {
                blocks.append(.quote(quote.joined(separator: " ")))
                quote = []
            }
        }
        func flushAll() {
            flushParagraph(); flushBullets(); flushOrdered(); flushQuote()
        }

        for rawLine in source.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(rawLine)

            if var fence = codeLines {
                if line.hasPrefix("```") {
                    blocks.append(.code(lang: codeLang, body: fence.joined(separator: "\n")))
                    codeLines = nil
                    codeLang = nil
                } else {
                    fence.append(line)
                    codeLines = fence
                }
                continue
            }
            if line.hasPrefix("```") {
                flushAll()
                codeLines = []
                codeLang = String(line.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                continue
            }
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty {
                flushAll()
                continue
            }
            if trimmed == "---" || trimmed == "***" {
                flushAll()
                blocks.append(.divider)
                continue
            }
            if let level = headingLevel(trimmed) {
                flushAll()
                blocks.append(.heading(level: level, text: String(trimmed.dropFirst(level))))
                continue
            }
            if trimmed.hasPrefix("> ") || trimmed == ">" {
                flushParagraph(); flushBullets(); flushOrdered()
                quote.append(String(trimmed.dropFirst(trimmed == ">" ? 1 : 2)))
                continue
            }
            if trimmed.hasPrefix("- ") || trimmed.hasPrefix("* ") || trimmed.hasPrefix("• ") {
                flushParagraph(); flushOrdered(); flushQuote()
                bullets.append(String(trimmed.dropFirst(2)))
                continue
            }
            if let rest = orderedPrefix(trimmed) {
                flushParagraph(); flushBullets(); flushQuote()
                ordered.append(rest)
                continue
            }
            flushBullets(); flushOrdered(); flushQuote()
            paragraph.append(trimmed)
        }
        if let fence = codeLines {
            blocks.append(.code(lang: codeLang, body: fence.joined(separator: "\n")))
        }
        flushAll()
        return blocks
    }

    private func headingLevel(_ line: String) -> Int? {
        guard line.hasPrefix("#") else { return nil }
        var level = 0
        for ch in line {
            if ch == "#" { level += 1 } else { break }
        }
        guard level <= 6, line.count > level, line[line.index(line.startIndex, offsetBy: level)] == " " else { return nil }
        return level
    }

    private func orderedPrefix(_ line: String) -> String? {
        var digits = 0
        for ch in line {
            if ch.isNumber { digits += 1 } else { break }
        }
        guard digits > 0, digits < 3 else { return nil }
        let after = line.dropFirst(digits)
        if after.hasPrefix(". ") || after.hasPrefix(") ") {
            return String(after.dropFirst(2))
        }
        return nil
    }

    // MARK: - Rendering

    @ViewBuilder
    private func blockView(_ block: Block) -> some View {
        switch block {
        case .paragraph(let text):
            inline(text)
        case .heading(let level, let text):
            inline(text)
                .font(level <= 2 ? .title3.bold() : .subheadline.bold())
                .padding(.top, 2)
        case .code(let lang, let body):
            VStack(alignment: .leading, spacing: 4) {
                if let lang, !lang.isEmpty {
                    Text(lang.uppercased())
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(Theme.subtleText)
                }
                Text(body)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.subtleText.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
        case .bullet(let items):
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    HStack(alignment: .top, spacing: 6) {
                        Text("•")
                        inline(item)
                    }
                }
            }
        case .ordered(let items):
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(items.enumerated()), id: \.offset) { idx, item in
                    HStack(alignment: .top, spacing: 6) {
                        Text("\(idx + 1).").monospacedDigit()
                        inline(item)
                    }
                }
            }
        case .quote(let text):
            HStack(spacing: 8) {
                Rectangle().fill(Theme.subtleText.opacity(0.4)).frame(width: 3)
                inline(text).font(.callout.italic())
            }
        case .divider:
            Divider()
        }
    }

    @ViewBuilder
    private func inline(_ text: String) -> some View {
        if let attributed = highlightedAttributed(text) {
            Text(attributed)
        } else {
            Text(text)
        }
    }

    /// Markdown-resolved attributed string with the optional search
    /// highlight applied; nil when the markdown can't be parsed.
    private func highlightedAttributed(_ text: String) -> AttributedString? {
        guard var attributed = try? AttributedString(
            markdown: text,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        ) else { return nil }
        if let highlight {
            attributed = MarkdownText.applyingHighlight(highlight, to: attributed)
        }
        return attributed
    }
}

// MARK: - Search highlight

extension MarkdownText {
    /// Paints every case- and diacritic-insensitive occurrence of `term`
    /// with the accent color behind the glyphs.
    ///
    /// Matching runs on the resolved characters (plain string mirror of the
    /// attributed string), so markdown markers already stripped by the
    /// renderer can't skew the offsets; the String.Index ranges are then
    /// mapped back onto the attributed string 1:1.
    static func applyingHighlight(_ term: String, to source: AttributedString) -> AttributedString {
        var attributed = source
        let plain = String(attributed.characters)
        var searchStart = plain.startIndex
        while let found = plain.range(
            of: term,
            options: [.caseInsensitive, .diacriticInsensitive],
            range: searchStart..<plain.endIndex,
            locale: nil
        ) {
            let startOffset = plain.distance(from: plain.startIndex, to: found.lowerBound)
            let length = plain.distance(from: found.lowerBound, to: found.upperBound)
            let aStart = attributed.index(attributed.startIndex, offsetByCharacters: startOffset)
            let aEnd = attributed.index(aStart, offsetByCharacters: length)
            attributed[aStart..<aEnd].backgroundColor = Theme.accent.opacity(0.35)
            searchStart = found.upperBound
        }
        return attributed
    }
}
