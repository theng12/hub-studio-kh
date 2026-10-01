import AppKit
import SwiftUI

// The Studio Hub design tokens: one place for the type scale, the spacing grid, the corner radii, the
// status colours, the status badge, the shared page scaffold and the row/section/empty-state pieces.
//
// Why this file exists: before it, 13 pages each wrote their own `.padding(28)`, fonts came from 22
// different specs (157 of them the 10-point `.caption`), radii were 6, 8, 10 and 12, and the same state
// was a green capsule on one page and plain orange text on another. The Gateway app was drawn from the
// same table (`DS` in its PageKit), so a fleet reads the same in both. Change a value here and every
// page follows; do not write a size, a radius or a status colour inline.
//
// Nothing here knows about the Hub's data. The tiers that decide a disk or a memory colour live with
// the data (`NativeFleetMachine.diskTier` / `.memoryTier`); this file only says how a tier looks.

private struct HubWindowWidthKey: EnvironmentKey { static let defaultValue: CGFloat = 0 }

extension EnvironmentValues {
    /// The width of the window's content (sidebar included), set once at the root of the app's view. A
    /// view that has to choose between two layouts reads it here; nothing measures itself.
    var hubWindowWidth: CGFloat {
        get { self[HubWindowWidthKey.self] }
        set { self[HubWindowWidthKey.self] = newValue }
    }
}

// MARK: - Spacing, radii

/// The 4-point grid, with 6 as the one "tight" step. Page margin is the same on every page.
enum HubSpace {
    /// Icon to text, badge inner gap.
    static let xs: CGFloat = 4
    /// Title to subtitle, label to control.
    static let tight: CGFloat = 6
    /// Row padding, button gap.
    static let s: CGFloat = 8
    /// Between related controls, card header to body.
    static let m: CGFloat = 12
    /// Card padding, form row gap, section title to content.
    static let l: CGFloat = 16
    /// Between sections and cards; form to preview gutter.
    static let xl: CGFloat = 24
    /// The page margin, on all four sides, on every page.
    static let page: CGFloat = 28
    /// The sidebar, the same width as the Gateway's.
    static let sidebar: CGFloat = 220
}

enum HubRadius {
    /// Card, panel, banner, preview.
    static let card: CGFloat = 8
    /// Button, field, thumbnail, code block - anything smaller than a card.
    static let control: CGFloat = 6
    /// A sheet or modal, the only 12.
    static let sheet: CGFloat = 12
}

// MARK: - Type scale

/// Points, SF Pro, no half sizes, no bold. Weights are regular, medium (table headers only) and semibold.
enum HubType {
    /// Page title.
    static let pageTitle = Font.system(size: 26, weight: .semibold)
    /// Page subtitle, row body text.
    static let body = Font.system(size: 13)
    /// Section header.
    static let section = Font.system(size: 15, weight: .semibold)
    /// Row title, card title, form label.
    static let rowTitle = Font.system(size: 13, weight: .semibold)
    /// Secondary text, help text, table cells.
    static let secondary = Font.system(size: 12)
    /// Captions, metadata, table headers.
    static let caption = Font.system(size: 11)
    /// Badges and table headers.
    static let captionStrong = Font.system(size: 11, weight: .semibold)
    /// A stat's value.
    static let figure = Font.system(size: 22, weight: .semibold).monospacedDigit()
    /// Ids, paths, versions, tokens.
    static let mono = Font.system(size: 12, design: .monospaced)
    static let monoCaption = Font.system(size: 11, design: .monospaced)
    /// A glyph beside a row title.
    static let icon = Font.system(size: 15)
}

// MARK: - Status colours

/// One vocabulary for every state in the app, shared with the Gateway.
///
/// Colour is state, not decoration: a number is never coloured because it is big, and a badge always
/// names its state in words, so colour is never the only signal.
enum HubStatus: Equatable {
    /// Ready, working, healthy, completed.
    case ok
    /// Look soon: low disk, low memory, older version, needs a model.
    case warn
    /// Act now: down, offline, credential refused, failed, disk red.
    case bad
    /// Idle, unknown, cancelled, not applicable.
    case off
    /// Facts that are never alarms: a role, a new version.
    case info

    var color: Color {
        switch self {
        case .ok: return Color(nsColor: .systemGreen)
        case .warn: return Color(nsColor: .systemOrange)
        case .bad: return Color(nsColor: .systemRed)
        case .off: return Color(nsColor: .systemGray)
        case .info: return Color(nsColor: .systemBlue)
        }
    }

    /// The colour for *text* in this state: the status colour blended 40 % toward the label colour
    /// (black in light, white in dark). Plain system orange on white is about 2.3:1, and the same
    /// blend in the Gateway keeps both apps readable. Resolved per appearance, so it follows a
    /// switch between light and dark without a redraw of the data.
    var textColor: Color {
        let base: NSColor
        switch self {
        case .ok: base = .systemGreen
        case .warn: base = .systemOrange
        case .bad: base = .systemRed
        case .off: base = .systemGray
        case .info: base = .systemBlue
        }
        return Color(nsColor: NSColor(name: nil) { appearance in
            var resolved = base
            appearance.performAsCurrentDrawingAppearance {
                let from = base.usingColorSpace(.sRGB) ?? base
                let label = NSColor.labelColor.usingColorSpace(.sRGB) ?? NSColor.labelColor
                resolved = from.blended(withFraction: 0.4, of: label) ?? from
            }
            return resolved
        })
    }

    /// The status a disk or memory tier is drawn in. An unknown tier is grey, never green.
    init(_ tier: NativeResourceTier) {
        switch tier {
        case .ok: self = .ok
        case .warn: self = .warn
        case .bad: self = .bad
        case .unknown: self = .off
        }
    }
}

/// A state, drawn once the same way everywhere: tint fill, thin border of the same tint, 11-point
/// semibold text in the readable status colour, and an optional SF Symbol.
struct StatusBadge: View {
    let text: String
    var status: HubStatus = .off
    var symbol: String?

    var body: some View {
        HStack(spacing: HubSpace.xs) {
            if let symbol { Image(systemName: symbol).imageScale(.small) }
            Text(text)
        }
        .font(HubType.captionStrong)
        .foregroundStyle(status.textColor)
        .padding(.horizontal, HubSpace.s).padding(.vertical, 3)
        .background(status.color.opacity(0.14), in: Capsule())
        .overlay(Capsule().stroke(status.color.opacity(0.35), lineWidth: 1))
        .fixedSize()
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(text)
    }
}

/// A 4-point meter for a fraction of something (free disk, free memory), in a status colour.
struct MeterBar: View {
    let fraction: Double
    var status: HubStatus = .ok

    var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.primary.opacity(0.10))
                Capsule().fill(status.color)
                    .frame(width: max(0, min(1, fraction)) * proxy.size.width)
            }
        }
        .frame(height: 4)
        .accessibilityHidden(true)
    }
}

// MARK: - Buttons

extension View {
    /// The one primary action of a page region: accent-filled. Never two in a region.
    func hubPrimary() -> some View { buttonStyle(.borderedProminent) }
    /// Everything else: the default bordered button.
    func hubSecondary() -> some View { buttonStyle(.bordered) }
    /// A link-style action ("Open in Fleet").
    func hubTertiary() -> some View { buttonStyle(.link) }
    /// A button inside a row or a toolbar: small.
    func hubRowControl() -> some View { controlSize(.small) }
}

// MARK: - Page scaffold

/// Every page: the title and subtitle named like the sidebar row, the one page margin, content below.
///
/// `scrolls` is for pages that are one long column (Overview, Jobs, Connections, Settings); the
/// workspace pages are split panes that scroll inside themselves, so a page never resizes the window.
struct PageScaffold<Trailing: View, Content: View>: View {
    let title: String
    let subtitle: String
    var scrolls = false
    @ViewBuilder var trailing: Trailing
    @ViewBuilder var content: Content

    var body: some View {
        let page = VStack(alignment: .leading, spacing: HubSpace.xl) {
            HStack(alignment: .top, spacing: HubSpace.l) {
                VStack(alignment: .leading, spacing: HubSpace.tight) {
                    Text(title).font(HubType.pageTitle)
                    Text(subtitle).font(HubType.body).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
                trailing
            }
            content
        }
        .padding(HubSpace.page)
        .frame(maxWidth: .infinity, maxHeight: scrolls ? nil : .infinity, alignment: .topLeading)
        if scrolls {
            ScrollView { page }
        } else {
            page
        }
    }
}

extension PageScaffold where Trailing == EmptyView {
    init(title: String, subtitle: String, scrolls: Bool = false, @ViewBuilder content: () -> Content) {
        self.init(title: title, subtitle: subtitle, scrolls: scrolls, trailing: { EmptyView() }, content: content)
    }
}

// MARK: - Card, section, form row, empty state

/// A panel: control background, one point separator border, 8-point radius, no shadow, no nesting.
struct HubCard<Content: View>: View {
    var padding: CGFloat = HubSpace.l
    @ViewBuilder var content: Content

    var body: some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: HubRadius.card))
            .overlay(RoundedRectangle(cornerRadius: HubRadius.card)
                .stroke(Color(nsColor: .separatorColor), lineWidth: 1))
    }
}

extension View {
    /// The card surface without the padding, for a preview or a player that brings its own: the control
    /// background, a one point separator border and an 8-point radius, clipped to it.
    func hubCardSurface() -> some View {
        self
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: HubRadius.card))
            .clipShape(RoundedRectangle(cornerRadius: HubRadius.card))
            .overlay(RoundedRectangle(cornerRadius: HubRadius.card).stroke(Color(nsColor: .separatorColor), lineWidth: 1))
    }
}

/// A titled group of settings or facts: a 15-point section header over a card, 24 points from the next.
struct HubSection<Trailing: View, Content: View>: View {
    let title: String
    var detail: String?
    @ViewBuilder var trailing: Trailing
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: HubSpace.m) {
            HStack(alignment: .firstTextBaseline, spacing: HubSpace.m) {
                Text(title).font(HubType.section)
                Spacer(minLength: 0)
                trailing
            }
            if let detail {
                Text(detail).font(HubType.secondary).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HubCard { VStack(alignment: .leading, spacing: HubSpace.m) { content } }
        }
    }
}

extension HubSection where Trailing == EmptyView {
    init(_ title: String, detail: String? = nil, @ViewBuilder content: () -> Content) {
        self.init(title: title, detail: detail, trailing: { EmptyView() }, content: content)
    }
}

/// A form row: the label above (13 semibold), the control under it left-aligned, an optional line of
/// help (11 secondary). Labels and controls start at the same x on every page, instead of a label on
/// the left and a popup centred in the column.
struct LabeledRow<Control: View>: View {
    let label: String
    var help: String?
    @ViewBuilder var control: Control

    var body: some View {
        VStack(alignment: .leading, spacing: HubSpace.tight) {
            Text(label).font(HubType.rowTitle)
            control.frame(maxWidth: .infinity, alignment: .leading)
            if let help {
                Text(help).font(HubType.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// A label on the left and its value on the right, on one baseline. `LabeledContent` only does this
/// inside a grouped `Form`; outside one it sets the value right beside the label, so a column of
/// settings did not line up. The value wraps to the right when it is long (a path, a fingerprint).
struct ValueRow<Value: View>: View {
    let label: String
    @ViewBuilder var value: Value

    init(_ label: String, @ViewBuilder content: () -> Value) {
        self.label = label
        self.value = content()
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: HubSpace.m) {
            Text(label).font(HubType.body)
            Spacer(minLength: HubSpace.m)
            value.font(HubType.body).multilineTextAlignment(.trailing)
        }
    }
}

extension ValueRow where Value == Text {
    init(_ label: String, value: String) {
        self.init(label) { Text(value).foregroundStyle(.secondary) }
    }
}

/// Nothing to show: a 28-point light symbol, a title, a line of detail and, optionally, the one action.
struct EmptyState: View {
    let title: String
    let detail: String
    let symbol: String

    var body: some View {
        VStack(spacing: HubSpace.s) {
            Image(systemName: symbol).font(.system(size: 28, weight: .light)).foregroundStyle(.secondary)
            Text(title).font(HubType.rowTitle)
            Text(detail).font(HubType.secondary).foregroundStyle(.secondary)
                .multilineTextAlignment(.center).frame(maxWidth: 440)
        }
        .padding(HubSpace.xl).frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// A banner for a state the operator should read: a tinted card, a symbol, the message verbatim.
struct HubBanner<Actions: View>: View {
    var status: HubStatus
    var symbol: String
    var title: String?
    let message: String
    @ViewBuilder var actions: Actions

    var body: some View {
        HStack(alignment: .top, spacing: HubSpace.m) {
            Image(systemName: symbol).foregroundStyle(status.color)
            VStack(alignment: .leading, spacing: HubSpace.xs) {
                if let title { Text(title).font(HubType.rowTitle) }
                Text(message).font(HubType.secondary).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: HubSpace.s)
            actions
        }
        .padding(HubSpace.m)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(status.color.opacity(0.10), in: RoundedRectangle(cornerRadius: HubRadius.card))
        .overlay(RoundedRectangle(cornerRadius: HubRadius.card).stroke(status.color.opacity(0.35), lineWidth: 1))
    }
}

extension HubBanner where Actions == EmptyView {
    init(status: HubStatus, symbol: String, title: String? = nil, message: String) {
        self.init(status: status, symbol: symbol, title: title, message: message, actions: { EmptyView() })
    }
}
