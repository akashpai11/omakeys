pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui

// Generic 60%-layout key grid. Outline-only keys; a key gets a light accent
// fill only once it carries a remap, so a glance at the grid shows exactly
// what's been touched. The same component will later grow a heatmap mode
// that fills every key by press-frequency instead of remap state — same
// geometry, different data, per the "reuse one grid across modes" design.
Item {
  id: root

  // { "<physical-keyd-name>": "<target-keyd-name>" }
  property var remaps: ({})
  property string selectedCode: ""
  property color foreground: Color.popups.text
  property int unit: Style.space(30)
  property int gap: Style.space(3)

  // "remap" (default): outline keys, filled only when remapped. "heatmap":
  // same grid, filled by relative press frequency from `heatmapCounts`
  // instead — reusing one geometry across both, per the design brief.
  property string mode: "remap"
  property var heatmapCounts: ({})
  readonly property real maxHeatmapCount: {
    var max = 0
    for (var k in heatmapCounts) if (heatmapCounts[k] > max) max = heatmapCounts[k]
    return max
  }

  // Theme-derived, not a fixed ramp: the accent hue stays whatever the
  // active theme picked (a Nord theme reads cool, Gruvbox reads warm)
  // and only alpha over the existing surface varies with intensity —
  // alpha-blending is what the rest of the shell's own state fills
  // (hover/selected/etc.) already use, so this stays visually consistent
  // and safe across both light and dark theme variants.
  function heatFillFor(code) {
    if (maxHeatmapCount <= 0) return "transparent"
    var count = heatmapCounts[code] || 0
    if (count <= 0) return "transparent"
    var intensity = count / maxHeatmapCount
    return Util.alpha(Color.accent, 0.10 + intensity * 0.75)
  }

  // NuPhy (and most boards with a Mac/Win switch) don't just relabel the
  // bottom-row modifiers between modes — the physical key next to Space
  // sends a different keycode outright: leftmeta (Cmd) in Mac mode,
  // leftalt in Windows mode, with Alt/Option taking the other slot. The
  // grid has to track that, not just the caption, or a click on "the key
  // beside Space" would target the wrong physical key.
  property bool macStyle: true

  signal keySelected(string code, string label)

  readonly property var bottomRow: macStyle ? [
      { c: "leftcontrol", l: "Ctrl", w: 1.25 }, { c: "leftalt", l: "Option", w: 1.25 }, { c: "leftmeta", l: "Cmd", w: 1.25 },
      { c: "space", l: "", w: 6.25 },
      { c: "rightmeta", l: "Cmd", w: 1.25 }, { c: "rightalt", l: "Option", w: 1.25 }, { c: "rightcontrol", l: "Ctrl", w: 1.25 }
    ] : [
      { c: "leftcontrol", l: "Ctrl", w: 1.25 }, { c: "leftmeta", l: "Win", w: 1.25 }, { c: "leftalt", l: "Alt", w: 1.25 },
      { c: "space", l: "", w: 6.25 },
      { c: "rightalt", l: "Alt", w: 1.25 }, { c: "rightmeta", l: "Win", w: 1.25 }, { c: "rightcontrol", l: "Ctrl", w: 1.25 }
    ]

  readonly property var layout: [
    [
      { c: "esc", l: "Esc" }, { c: "1", l: "1" }, { c: "2", l: "2" }, { c: "3", l: "3" }, { c: "4", l: "4" },
      { c: "5", l: "5" }, { c: "6", l: "6" }, { c: "7", l: "7" }, { c: "8", l: "8" }, { c: "9", l: "9" },
      { c: "0", l: "0" }, { c: "minus", l: "-" }, { c: "equal", l: "=" }, { c: "backspace", l: "Bksp", w: 2 }
    ],
    [
      { c: "tab", l: "Tab", w: 1.5 }, { c: "q", l: "Q" }, { c: "w", l: "W" }, { c: "e", l: "E" }, { c: "r", l: "R" },
      { c: "t", l: "T" }, { c: "y", l: "Y" }, { c: "u", l: "U" }, { c: "i", l: "I" }, { c: "o", l: "O" },
      { c: "p", l: "P" }, { c: "leftbrace", l: "[" }, { c: "rightbrace", l: "]" }, { c: "backslash", l: "\\", w: 1.5 }
    ],
    [
      { c: "capslock", l: "Caps", w: 1.75 }, { c: "a", l: "A" }, { c: "s", l: "S" }, { c: "d", l: "D" }, { c: "f", l: "F" },
      { c: "g", l: "G" }, { c: "h", l: "H" }, { c: "j", l: "J" }, { c: "k", l: "K" }, { c: "l", l: "L" },
      { c: "semicolon", l: ";" }, { c: "apostrophe", l: "'" }, { c: "enter", l: "Enter", w: 2.25 }
    ],
    [
      { c: "leftshift", l: "Shift", w: 2.25 }, { c: "z", l: "Z" }, { c: "x", l: "X" }, { c: "c", l: "C" }, { c: "v", l: "V" },
      { c: "b", l: "B" }, { c: "n", l: "N" }, { c: "m", l: "M" }, { c: "comma", l: "," }, { c: "dot", l: "." },
      { c: "slash", l: "/" }, { c: "rightshift", l: "Shift", w: 2.75 }
    ],
    bottomRow
  ]

  implicitWidth: 15 * unit
  implicitHeight: layout.length * unit

  Column {
    anchors.fill: parent
    spacing: root.gap

    Repeater {
      model: root.layout
      Row {
        required property var modelData
        spacing: root.gap

        Repeater {
          model: parent.modelData
          Rectangle {
            id: key
            required property var modelData
            readonly property real widthUnits: modelData.w || 1
            readonly property bool remapped: !!root.remaps[modelData.c]
            readonly property bool selected: root.selectedCode === modelData.c
            readonly property int heatCount: root.heatmapCounts[modelData.c] || 0

            width: root.unit * widthUnits - root.gap
            height: root.unit - root.gap
            radius: Style.cornerRadius
            color: root.mode === "heatmap"
              ? root.heatFillFor(modelData.c)
              : (remapped ? Style.selectedFillFor(root.foreground, Color.accent) : "transparent")
            border.width: selected ? Math.max(1, Style.selectedBorderWidth || 1) : Style.normalBorderWidth
            border.color: selected ? Color.accent : Util.alpha(root.foreground, Style.normalBorderAlpha)

            Column {
              anchors.centerIn: parent
              spacing: 1
              Text {
                anchors.horizontalCenter: parent.horizontalCenter
                textFormat: Text.PlainText
                text: key.modelData.l
                color: key.selected ? Color.accent : root.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
              }
              Text {
                anchors.horizontalCenter: parent.horizontalCenter
                visible: root.mode === "heatmap" ? key.heatCount > 0 : key.remapped
                textFormat: Text.PlainText
                text: root.mode === "heatmap" ? String(key.heatCount) : ("→ " + (root.remaps[key.modelData.c] || ""))
                color: Color.accent
                font.family: Style.font.family
                font.pixelSize: Math.max(8, Style.font.caption - 2)
                elide: Text.ElideRight
              }
            }

            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.PointingHandCursor
              onClicked: root.keySelected(key.modelData.c, key.modelData.l)
            }
          }
        }
      }
    }
  }
}
