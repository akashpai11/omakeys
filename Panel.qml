pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "KeydKeys.js" as KeydKeys

Panel {
  id: root
  moduleName: "akashpai11.omakeys"
  ipcTarget: "akashpai11.omakeys"
  readonly property var service: bar && bar.shell ? bar.shell.serviceFor(moduleName) : null
  readonly property var snapshot: service ? service.snapshot : ({ keyboards: [], keyd: { installed: false, active: false } })
  readonly property var primary: service ? service.primary : null
  readonly property color foreground: Color.popups.text
  readonly property color muted: Qt.alpha(foreground, 0.68)

  readonly property string profileDir: Quickshell.env("HOME") + "/.config/omarchy/omakeys/profiles"
  readonly property string applyHelperPath: decodeURIComponent(
    Qt.resolvedUrl("scripts/apply_profile.py").toString().replace(/^file:\/\//, ""))
  readonly property string viaHelperPath: decodeURIComponent(
    Qt.resolvedUrl("scripts/probe_via.py").toString().replace(/^file:\/\//, ""))
  readonly property string heatmapQueryHelperPath: decodeURIComponent(
    Qt.resolvedUrl("scripts/heatmap_query.py").toString().replace(/^file:\/\//, ""))
  readonly property string setupHelperPath: decodeURIComponent(
    Qt.resolvedUrl("scripts/setup_permissions.py").toString().replace(/^file:\/\//, ""))

  property string tab: "remap"
  property var heatmapData: ({ total: 0, byKey: {} })
  property int heatmapRangeDays: 1

  function refreshHeatmap() {
    heatmapProc.command = ["/usr/bin/python3", heatmapQueryHelperPath, String(heatmapRangeDays)]
    heatmapProc.running = true
  }

  Process {
    id: heatmapProc
    stdout: StdioCollector { id: heatmapOut; waitForEnd: true }
    onRunningChanged: if (running) heatmapWatchdog.restart(); else heatmapWatchdog.stop()
    onExited: function(exitCode) {
      try {
        var res = JSON.parse(heatmapOut.text)
        root.heatmapData = { total: res.total || 0, byKey: res.byKey || {} }
      } catch (exception) { /* keep last-good data on a bad read */ }
    }
  }
  Timer { id: heatmapWatchdog; interval: 10000; onTriggered: if (heatmapProc.running) heatmapProc.running = false }

  onTabChanged: if (tab === "heatmap") refreshHeatmap()
  onHeatmapRangeDaysChanged: if (tab === "heatmap") refreshHeatmap()
  Timer { interval: 8000; running: root.opened && root.tab === "heatmap"; repeat: true; onTriggered: root.refreshHeatmap() }

  // phys -> { status: "checking"|"detected"|"not-detected"|"error", protocolVersion, error }
  property var viaCache: ({})
  readonly property var viaResult: selectedDevice ? (viaCache[selectedDevice.phys] || null) : null

  property var selectedDevice: null
  readonly property string deviceKey: selectedDevice && selectedDevice.vendorId
    ? (selectedDevice.vendorId + ":" + selectedDevice.productId) : ""
  property var profile: ({})
  property string selectedCode: ""
  property string selectedLabel: ""
  property bool applying: false
  property string applyStatus: ""
  // NuPhy-style boards switch which physical key next to Space sends Cmd vs
  // Alt depending on a Mac/Win mode set on the board itself. Global (not
  // per-device) for now — one toggle, remembered for the session.
  property bool macStyle: true

  function selectDevice(device) {
    selectedDevice = device
    selectedCode = ""
    selectedLabel = ""
    applyStatus = ""
    probeVia(device)
  }

  function probeVia(device, force) {
    if (!device || !device.hidraw) return
    var cached = viaCache[device.phys]
    if (!force && cached && cached.status !== "error") return
    var next = Object.assign({}, viaCache)
    next[device.phys] = { status: "checking" }
    viaCache = next
    viaProc.targetPhys = device.phys
    viaProc.command = ["/usr/bin/python3", viaHelperPath, device.hidraw]
    viaProc.running = true
  }

  Process {
    id: viaProc
    property string targetPhys: ""
    stdout: StdioCollector { id: viaOut; waitForEnd: true }
    onRunningChanged: if (running) viaWatchdog.restart(); else viaWatchdog.stop()
    onExited: function(exitCode) {
      var result
      try {
        var res = JSON.parse(viaOut.text)
        result = res.via
          ? { status: "detected", protocolVersion: res.protocolVersion }
          : { status: "not-detected", error: res.error || "" }
      } catch (exception) {
        result = { status: "error", error: String(exception) }
      }
      var next = Object.assign({}, root.viaCache)
      next[viaProc.targetPhys] = result
      root.viaCache = next
    }
  }
  // probe_via.py bounds its own read loop to ~1.5s worst case; this is
  // generous headroom on top of that for process startup, not the normal path.
  Timer { id: viaWatchdog; interval: 10000; onTriggered: if (viaProc.running) viaProc.running = false }

  // True when this account is missing the one-time access the plugin needs:
  // the keyd/input groups (heatmap) and the VIA udev rule (raw-HID probe).
  // Surfaced as a single "Grant access" action rather than two, since both
  // are granted by the same pkexec call.
  readonly property bool needsPermissionSetup:
    (service && service.heatmapPermissionIssue && !service.heatmapWatchingAnyDevice)
    || (!!viaResult && viaResult.status === "not-detected"
        && !!viaResult.error && viaResult.error.indexOf("Permission denied") !== -1)

  property bool settingUp: false
  property string setupStatus: ""

  function runSetup() {
    if (settingUp) return
    settingUp = true
    setupStatus = ""
    setupProc.command = ["/usr/bin/python3", setupHelperPath]
    setupProc.running = true
  }

  Process {
    id: setupProc
    stdout: StdioCollector { id: setupOut; waitForEnd: true }
    stderr: StdioCollector { id: setupErr; waitForEnd: true }
    onRunningChanged: if (running) setupWatchdog.restart(); else setupWatchdog.stop()
    onExited: function(exitCode) {
      root.settingUp = false
      try {
        var res = JSON.parse(setupOut.text)
        root.setupStatus = res.ok
          ? "Udev rule installed. Log out and back in to finish (group membership needs a fresh session)."
          : ("Failed: " + res.error)
      } catch (exception) {
        root.setupStatus = "Failed: " + (setupErr.text.trim() || String(exception))
      }
      // The udev rule takes effect immediately; re-probe so a true positive
      // shows up without waiting for the relogin the group grant still needs.
      if (root.selectedDevice) root.probeVia(root.selectedDevice, true)
    }
  }
  // Bounds the outer process, not just the pkexec call inside it (that one
  // already caps itself at 60s) — headroom for interpreter/process startup
  // plus the time a user takes to answer the polkit prompt.
  Timer { id: setupWatchdog; interval: 70000; onTriggered: if (setupProc.running) setupProc.running = false }

  onOpenedChanged: if (opened) {
    if (service) service.refresh()
    if (!selectedDevice && primary) selectDevice(primary)
    if (tab === "heatmap") refreshHeatmap()
  }
  onPrimaryChanged: if (!selectedDevice && primary) selectDevice(primary)

  function setRemap(target) {
    if (!selectedCode) return
    var next = Object.assign({}, profile)
    if (target) next[selectedCode] = target
    else delete next[selectedCode]
    profile = next
  }

  function applyProfile() {
    if (!deviceKey || applying) return
    applying = true
    applyStatus = ""
    applyProc.command = ["/usr/bin/python3", applyHelperPath, deviceKey, Qt.btoa(JSON.stringify(profile))]
    applyProc.running = true
  }

  Process {
    id: applyProc
    stdout: StdioCollector { id: applyOut; waitForEnd: true }
    stderr: StdioCollector { id: applyErr; waitForEnd: true }
    onRunningChanged: if (running) applyWatchdog.restart(); else applyWatchdog.stop()
    onExited: function(exitCode) {
      root.applying = false
      try {
        var res = JSON.parse(applyOut.text)
        root.applyStatus = res.ok ? "Applied — keyd reloaded." : ("Failed: " + res.error)
      } catch (exception) {
        root.applyStatus = "Failed: " + (applyErr.text.trim() || String(exception))
      }
    }
  }
  // Same reasoning as setupWatchdog above: apply_profile.py's own pkexec
  // call caps at 120s, so this outer bound gives it room to actually finish.
  Timer { id: applyWatchdog; interval: 130000; onTriggered: if (applyProc.running) applyProc.running = false }

  FileView {
    id: profileFile
    path: root.deviceKey ? root.profileDir + "/" + root.deviceKey.replace(":", "_") + ".json" : ""
    watchChanges: false
    printErrors: false
    onLoaded: {
      try {
        var parsed = JSON.parse(text())
        root.profile = (parsed && typeof parsed === "object") ? parsed : {}
      } catch (exception) { root.profile = {} }
    }
    onLoadFailed: root.profile = {}
  }
  onDeviceKeyChanged: profile = {}

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰌌"
    tooltipText: root.primary
      ? root.primary.name + (root.primary.bus ? " (" + root.primary.bus + ")" : "")
      : "No keyboard detected"
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.LeftButton) root.toggle()
      else if (buttonCode === Qt.MiddleButton && root.service) root.service.refresh()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    bar: root.bar
    owner: root
    open: root.opened
    contentWidth: panel.fittedContentWidth(Style.space(540))
    contentHeight: panel.fittedContentHeight(mainColumn.implicitHeight)

    ColumnLayout {
      id: mainColumn
      anchors.fill: parent
      spacing: Style.space(8)
      Keys.onEscapePressed: root.close()

      PanelHero {
        Layout.fillWidth: true
        title: "Keyboard"
        meta: (root.snapshot.keyboards || []).length + " detected"
        foreground: root.foreground
        iconComponent: Component {
          Text { text: "󰌌"; color: root.foreground; font.family: Style.font.family; font.pixelSize: Style.font.display }
        }
        trailingControl: Component {
          Button {
            iconText: "󰑐"
            tooltipText: "Rescan for keyboards"
            focusable: true
            iconSpinning: root.service && root.service.refreshing
            enabled: root.service && !root.service.refreshing
            onClicked: root.service.refresh()
          }
        }
      }

      Text {
        Layout.fillWidth: true
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: root.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        text: !root.snapshot.keyd || !root.snapshot.keyd.installed
          ? "keyd not installed — universal remapping unavailable."
          : root.snapshot.keyd.active
            ? "keyd running — remaps apply live."
            : "keyd installed but not running."
      }

      RowLayout {
        Layout.fillWidth: true
        spacing: Style.space(8)
        visible: root.needsPermissionSetup || root.setupStatus !== ""

        Text {
          Layout.fillWidth: true
          textFormat: Text.PlainText
          wrapMode: Text.Wrap
          color: root.setupStatus.indexOf("Failed") === 0 ? Color.urgent : root.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          text: root.setupStatus || "VIA detection and the heatmap need one-time access to this account."
        }
        Button {
          text: root.settingUp ? "Setting up…" : "Grant access"
          tooltipText: "One pkexec prompt: adds this account to the keyd/input groups and installs the VIA udev rule"
          foreground: root.foreground
          focusable: true
          bordered: true
          enabled: !root.settingUp
          onClicked: root.runSetup()
        }
      }

      PanelSeparator { Layout.fillWidth: true; foreground: root.foreground }

      RowLayout {
        Layout.fillWidth: true
        spacing: Style.space(4)
        Button {
          text: "Remap"
          foreground: root.foreground
          selected: root.tab === "remap"
          focusable: true
          onClicked: root.tab = "remap"
        }
        Button {
          text: "Heatmap"
          foreground: root.foreground
          selected: root.tab === "heatmap"
          focusable: true
          onClicked: root.tab = "heatmap"
        }
        Item { Layout.fillWidth: true }
      }

      // ------------------------------------------------------------ Remap
      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(8)
        visible: root.tab === "remap"

        Flow {
          Layout.fillWidth: true
          spacing: Style.space(4)
          Repeater {
            model: root.snapshot.keyboards || []
            Button {
              required property var modelData
              text: modelData.name
              foreground: root.foreground
              selected: !!root.selectedDevice && root.selectedDevice.phys === modelData.phys
              focusable: true
              onClicked: root.selectDevice(modelData)
            }
          }
        }

        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(6)
          visible: !!root.selectedDevice

          Text {
            text: root.selectedDevice
              ? [
                  root.selectedDevice.connectionLabel || root.selectedDevice.bus,
                  root.selectedDevice.hidraw
                    ? (root.selectedDevice.pollingHz
                        ? root.selectedDevice.pollingHz + " Hz"
                        : (root.selectedDevice.bus === "bluetooth" ? "polling n/a over Bluetooth" : "polling n/a"))
                    : ""
                ].filter(function(s) { return s !== "" }).join(" · ")
              : ""
            color: root.muted
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
          Item { Layout.fillWidth: true }
          Text {
            visible: !!root.selectedDevice && root.selectedDevice.hidraw && !!root.viaResult
            text: !root.viaResult ? ""
              : root.viaResult.status === "checking" ? "Checking for VIA…"
              : root.viaResult.status === "detected" ? "VIA detected (v" + root.viaResult.protocolVersion + ")"
              : "VIA not detected"
            color: root.viaResult && root.viaResult.status === "detected" ? Color.accent : root.muted
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            font.bold: root.viaResult && root.viaResult.status === "detected"
          }
          Button {
            text: root.macStyle ? "Mac" : "Windows"
            tooltipText: "Bottom-row layout: which physical key beside Space sends Cmd vs Alt"
            foreground: root.foreground
            focusable: true
            bordered: true
            visible: !!root.selectedDevice
            onClicked: root.macStyle = !root.macStyle
          }
        }

        Text {
          Layout.fillWidth: true
          visible: root.deviceKey === ""
          text: root.selectedDevice
            ? "This keyboard has no known VID:PID, so keyd can't target it individually yet."
            : "Select a keyboard above to remap it."
          textFormat: Text.PlainText
          wrapMode: Text.Wrap
          color: root.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }

        ColumnLayout {
          Layout.fillWidth: true
          spacing: Style.space(8)
          visible: root.deviceKey !== ""

          Flickable {
            Layout.fillWidth: true
            Layout.preferredHeight: grid.implicitHeight
            contentWidth: grid.implicitWidth
            contentHeight: grid.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds

            KeyGrid {
              id: grid
              foreground: root.foreground
              remaps: root.profile
              selectedCode: root.selectedCode
              macStyle: root.macStyle
              onKeySelected: function(code, label) { root.selectedCode = code; root.selectedLabel = label }
            }
          }

          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(8)
            visible: root.selectedCode !== ""

            Text {
              text: root.selectedLabel + " →"
              color: root.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.body
            }
            SearchableDropdown {
              Layout.fillWidth: true
              foreground: root.foreground
              accent: Color.accent
              showLabel: false
              placeholderText: "identity (unmapped)"
              options: KeydKeys.keydKeys
              value: root.profile[root.selectedCode] || ""
              onChanged: function(v) { root.setRemap(v) }
            }
            Button {
              text: "Clear"
              foreground: root.foreground
              focusable: true
              bordered: true
              enabled: !!root.profile[root.selectedCode]
              onClicked: root.setRemap("")
            }
          }

          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(8)

            Button {
              text: root.applying ? "Applying…" : "Apply to keyd"
              tooltipText: "Writes /etc/keyd/" + root.deviceKey.replace(":", "_") + ".conf via a one-time authentication prompt"
              foreground: root.foreground
              focusable: true
              bordered: true
              enabled: !root.applying && root.snapshot.keyd && root.snapshot.keyd.installed
              onClicked: root.applyProfile()
            }
            Text {
              Layout.fillWidth: true
              text: root.applyStatus || (Object.keys(root.profile).length === 0 ? "Click a key to remap it." : "")
              textFormat: Text.PlainText
              wrapMode: Text.Wrap
              color: root.applyStatus.indexOf("Failed") === 0 ? Color.urgent : root.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }
        }
      }

      // ---------------------------------------------------------- Heatmap
      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(8)
        visible: root.tab === "heatmap"

        Text {
          Layout.fillWidth: true
          textFormat: Text.PlainText
          wrapMode: Text.Wrap
          color: root.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          text: (service && service.heatmapPermissionIssue && !service.heatmapWatchingAnyDevice)
            ? "Needs a relogin to start counting — this account was just added to the input group."
            : "Global across every keyboard, counted after any remap — not password fields or the lock screen."
        }

        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(10)
          Text {
            text: root.heatmapData.total + " key" + (root.heatmapData.total === 1 ? "" : "s") + (root.heatmapRangeDays === 7 ? " this week" : " today")
            color: root.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            font.bold: true
          }
          Item { Layout.fillWidth: true }
          Button {
            text: "7d"
            foreground: root.foreground
            selected: root.heatmapRangeDays === 7
            focusable: true
            onClicked: root.heatmapRangeDays = root.heatmapRangeDays === 7 ? 1 : 7
          }
          Button {
            iconText: "󰑐"
            tooltipText: "Refresh"
            focusable: true
            onClicked: root.refreshHeatmap()
          }
        }

        Flickable {
          Layout.fillWidth: true
          Layout.preferredHeight: heatGrid.implicitHeight
          contentWidth: heatGrid.implicitWidth
          contentHeight: heatGrid.implicitHeight
          clip: true
          boundsBehavior: Flickable.StopAtBounds

          KeyGrid {
            id: heatGrid
            foreground: root.foreground
            mode: "heatmap"
            heatmapCounts: root.heatmapData.byKey
            macStyle: root.macStyle
          }
        }
      }
    }
  }
}
