import SwiftUI

/// What's New, read from the notes that ship inside the app.
///
/// Packaging generates the bundle's `release-notes.md` from CHANGELOG.md, keeping the
/// plain-language changes in the app tied to the same version as the release record.
///
/// It is shown once after an update and then left alone. Two decisions worth stating:
///
///   * A first run does not open it. There is nothing to compare against, and a brand-new
///     operator wants the window they just opened, not a wall of release notes.
///   * `UserDefaults` holds the seen version, so a Mac that copied the app over the top of an
///     older one announces the new version without anyone asking it to.
enum ReleaseNotes {
    /// The version this build is, from the bundle rather than a constant that can drift.
    static var currentVersion: String {
        (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String)
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) } ?? "unknown"
    }

    static var title: String { "Studio Hub \(currentVersion)" }

    /// The whole notes file, or a plain explanation if the bundle is missing it.
    static func text() -> String {
        guard let url = Bundle.main.url(forResource: "release-notes", withExtension: "md"),
              let body = try? String(contentsOf: url, encoding: .utf8),
              !body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return """
            # What's new

            These notes were not found inside the app. The full history is in the `CHANGELOG.md`
            that ships beside it.
            """
        }
        return body
    }

    /// The notes with their heading lines removed, for the compact list on the Overview page.
    static func bulletSummary(limit: Int = 4) -> [String] {
        var found: [String] = []
        for line in text().split(separator: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard trimmed.hasPrefix("- "), !trimmed.hasPrefix("---") else { continue }
            found.append(String(trimmed.dropFirst(2)))
            if found.count == limit { break }
        }
        return found
    }
}

/// Remembers which release notes have been shown, so an update announces itself exactly once.
enum ReleaseNotesSeen {
    private static let key = "studiohub.release-notes.seen-version"

    static var version: String? {
        get { UserDefaults.standard.string(forKey: key) }
        set { UserDefaults.standard.set(newValue, forKey: key) }
    }

    /// True when this launch should open What's New by itself.
    ///
    /// Recording the version even when nothing is shown is deliberate: an app that has never run
    /// before is not an upgrade, and treating it as one would greet every new install with a wall
    /// of notes.
    static func shouldAnnounce() -> Bool {
        let current = ReleaseNotes.currentVersion
        let seen = version
        if seen != current { version = current }
        guard let seen, seen != current else { return false }
        return true
    }
}

/// A small Markdown renderer, enough for the notes files and nothing more.
///
/// A third-party Markdown view would be a dependency for one screen, and the system Markdown
/// parser only handles inline syntax, not the headings and bullets these notes are made of.
struct MarkdownText: View {
    let markdown: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(Self.blocks(from: markdown).enumerated()), id: \.offset) { _, block in
                switch block {
                case .heading(let level, let text):
                    Text(Self.inline(text))
                        // 15 semibold for the release headings, 13 semibold under them: the type
                        // scale every other page uses, written out here because this file is built
                        // without the design file.
                        .font(level <= 2 ? .system(size: 15, weight: .semibold) : .system(size: 13, weight: .semibold))
                        .padding(.top, level <= 2 ? 12 : 4)
                case .bullet(let text):
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("•").foregroundStyle(.secondary)
                        Text(Self.inline(text)).fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(.leading, 4)
                case .paragraph(let text):
                    Text(Self.inline(text)).fixedSize(horizontal: false, vertical: true)
                case .rule:
                    Divider()
                }
            }
        }
        .textSelection(.enabled)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    enum Block {
        case heading(level: Int, text: String)
        case bullet(String)
        case paragraph(String)
        case rule
    }

    static func blocks(from markdown: String) -> [Block] {
        var blocks: [Block] = []
        var paragraph: [String] = []

        func flush() {
            guard !paragraph.isEmpty else { return }
            blocks.append(.paragraph(paragraph.joined(separator: " ")))
            paragraph = []
        }

        for raw in markdown.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty {
                flush()
                continue
            }
            if line == "---" {
                flush()
                blocks.append(.rule)
                continue
            }
            if line.hasPrefix("#") {
                flush()
                let level = line.prefix(while: { $0 == "#" }).count
                let text = line.drop(while: { $0 == "#" }).trimmingCharacters(in: .whitespaces)
                blocks.append(.heading(level: level, text: text))
                continue
            }
            if line.hasPrefix("- ") {
                flush()
                blocks.append(.bullet(String(line.dropFirst(2))))
                continue
            }
            paragraph.append(line)
        }
        flush()
        return blocks
    }

    /// Bold and code spans, which is all the notes use beyond headings and bullets.
    static func inline(_ text: String) -> AttributedString {
        (try? AttributedString(markdown: text,
                                options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(text)
    }
}

struct ReleaseNotesView: View {
    var body: some View {
        ScrollView {
            MarkdownText(markdown: ReleaseNotes.text())
                .padding(24)
        }
    }
}
