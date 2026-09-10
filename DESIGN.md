# Native shell design

Mode: Operate. Follow the owner-approved native macOS app direction and preserve existing web workspace styles. AppKit owns the window, menu bar and lifecycle; SwiftUI owns the functional sidebar and first-run/recovery state; WKWebView retains the existing editor between section changes.

Navigation order: Audio, Transcription, Image, Music, Video, Chat. SF Symbols and system typography, native control sizing/focus, a restrained selection highlight and platform window background. Hub is management infrastructure, not a seventh generation function. No dashboard metric cards or invented readiness numbers.

First launch: model folder selection or default library. Loading: explicit engine state. Ready: existing functional editor. Missing SSD or startup error: actionable explanation and engine log. Not-installed function: honest availability description and link back to Audio. Closing the window keeps the process alive; explicit Quit warns about active work before stopping the owned engine. Start at login is opt-in and reads actual macOS registration status.

Native minimum window 1040×680; existing web workspace remains scrollable. Separate builds must be inspected at default and minimum window sizes. Shell changes do not imply all engines, microphone, offline execution or Sonoma are qualified.
