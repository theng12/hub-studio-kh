#!/bin/bash
# Writes docs/appcast.xml for the DMG it is given. GitHub Pages serves /docs on main, and Sparkle
# reads https://theng12.github.io/hub-studio-kh/appcast.xml.
#
# The EdDSA signature covers THESE exact DMG bytes: a re-cut DMG must regenerate its item, because
# a stale signature fails silently - the app simply never updates.
#
#   bash packaging/appcast.sh "dist/Hub Studio KH-0.6.261.dmg" [published-asset-url]
#
# The item's version is read from the DMG's own bundle, never from this checkout (2026-11-24: an
# item named 1.4.419 over 1.4.420's bytes, appended "successfully"). A DMG whose bundle disagrees
# with VERSION is refused here rather than described wrongly on the feed.
#
# Pass the URL GitHub actually stored once the release exists: it rewrites spaces to dots
# (`Hub Studio KH-0.6.260.dmg` -> `Hub.Studio.KH-0.6.260.dmg`) under the version's tag. The
# signature covers the file bytes, not the URL, so a wrong URL only fails at download time.
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { printf 'Stopped: %s\n' "$1" >&2; exit 1; }

dmg="${1:?usage: packaging/appcast.sh <dist-dmg> [published-asset-url] [appcast-path]}"
[ -f "$dmg" ] || fail "no DMG at $dmg"
app="Hub Studio KH"
sign="vendor/khsparkle/sign_update"
private_key="${HUB_SPARKLE_PRIVATE:-$HOME/.kh-sparkle/local.studiohub.standalone/private.key}"
# The gate writes the item somewhere throwaway so a rejected artifact leaves the feed untouched.
appcast="${3:-docs/appcast.xml}"

[ -x "$sign" ] || fail "the vendored sign_update is missing at $sign"
[ -f "$private_key" ] || fail "no Sparkle private key at $private_key - set HUB_SPARKLE_PRIVATE"
chmod 600 "$private_key" 2>/dev/null || true

mnt="$(mktemp -d "${TMPDIR:-/tmp}/hub-appcast.XXXXXX")"
trap 'diskutil eject "$mnt" >/dev/null 2>&1 || true; rm -rf "$mnt"' EXIT
diskutil image attach --readOnly --mountOptions nobrowse --mountPoint "$mnt" "$dmg" >/dev/null \
  || fail "cannot mount $dmg"
plist="$mnt/$app.app/Contents/Info.plist"
[ -f "$plist" ] || fail "the DMG has no $app.app/Contents/Info.plist"
version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$plist")"
[ -n "$version" ] || fail "the DMG's bundle has no CFBundleShortVersionString"
checkout="$(cat VERSION)"
[ "$version" = "$checkout" ] \
  || fail "the DMG's bundle says $version but VERSION says $checkout - the item would describe the wrong release"
diskutil eject "$mnt" >/dev/null 2>&1 || true

asset="$(basename "$dmg" | tr ' ' '.')"
url="${2:-https://github.com/theng12/hub-studio-kh/releases/download/${version}/${asset}}"
length="$(stat -f %z "$dmg")"

# -p prints just the EdDSA signature; the appcast carries it as sparkle:edSignature. Its failures
# arrive as text on stdout, so the shape is checked too: an error sentence must never be written
# into the item as a signature.
signature="$("$sign" --ed-key-file "$private_key" -p "$dmg")" \
  || fail "sign_update refused $private_key: $signature"
[[ "$signature" =~ ^[A-Za-z0-9+/]{86}==$ ]] || fail "sign_update printed no signature: $signature"

mkdir -p "$(dirname "$appcast")"
[ -f "$appcast" ] || cat > "$appcast" <<XML
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>$app</title>
    <link>https://theng12.github.io/hub-studio-kh/appcast.xml</link>
    <description>$app releases</description>
    <language>en</language>
  </channel>
</rss>
XML

python3 - "$appcast" "$version" "$url" "$signature" "$length" <<'PY'
import sys, datetime, xml.etree.ElementTree as ET

path, version, url, signature, length = sys.argv[1:6]
tree = ET.parse(path)
channel = tree.getroot().find("channel")
SPARKLE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
ET.register_namespace("sparkle", SPARKLE)

def item_version(item):
    return (item.findtext("{%s}shortVersionString" % SPARKLE)
            or item.findtext("title") or "").strip()

if any(item_version(item) == version for item in channel.findall("item")):
    sys.exit(f"refused: {path} already carries an item for {version}; one version = one item")

item = ET.Element("item")
ET.SubElement(item, "title").text = version
ET.SubElement(item, "pubDate").text = datetime.datetime.now(datetime.timezone.utc).strftime(
    "%a, %d %b %Y %H:%M:%S +0000")
ET.SubElement(item, "{%s}version" % SPARKLE).text = version
ET.SubElement(item, "{%s}shortVersionString" % SPARKLE).text = version
ET.SubElement(item, "{%s}minimumSystemVersion" % SPARKLE).text = "14.0"
enclosure = ET.SubElement(item, "enclosure")
enclosure.set("url", url)
enclosure.set("length", length)
enclosure.set("type", "application/octet-stream")
enclosure.set("{%s}version" % SPARKLE, version)
enclosure.set("{%s}shortVersionString" % SPARKLE, version)
enclosure.set("{%s}edSignature" % SPARKLE, signature)

items = channel.findall("item")
channel.insert(list(channel).index(items[0]) if items else len(channel), item)
tree.write(path, encoding="utf-8", xml_declaration=True)

# Read back what was written: an append that reports success over a different item is the
# failure this check exists for (row 20261125-4).
written = [i for i in ET.parse(path).getroot().find("channel").findall("item")
           if item_version(i) == version]
assert len(written) == 1, f"{path} holds {len(written)} items for {version}"
got = written[0].find("enclosure")
assert got.get("length") == length and got.get("url") == url, "the written item is not this DMG"
assert got.get("{%s}edSignature" % SPARKLE) == signature, "the written signature is not this DMG's"
print(f"wrote {path}: item {version}, length {length}, url {url.rsplit('/', 1)[-1]}, "
      f"signature {signature[:12]}...")
PY
