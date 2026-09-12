import QtQuick
import Quickshell
import Quickshell.Io

// Polls udev for connected keyboards and keyd's install/service state, and
// exposes the result to Panel.qml as `snapshot`. Read-only by design: this
// service never writes device or system state, it only reports it.
Item {
  id: root
  property var shell: null
  property var snapshot: ({ keyboards: [], keyd: { installed: false, active: false } })
  property string error: ""
  readonly property bool refreshing: collector.running
  readonly property string helperPath: decodeURIComponent(
    Qt.resolvedUrl("scripts/detect_devices.py").toString().replace(/^file:\/\//, ""))
  readonly property string heatmapDaemonPath: decodeURIComponent(
    Qt.resolvedUrl("scripts/heatmap_daemon.py").toString().replace(/^file:\/\//, ""))

  // Set once the daemon's stderr confirms it's actually reading a device —
  // the process can be alive but doing nothing if it started before the
  // user's `input` group membership took effect (needs a relogin).
  property bool heatmapWatchingAnyDevice: false
  property bool heatmapPermissionIssue: false

  // The keyboard everything else (bar chip label, the eventual remap grid)
  // should default to: the first externally-connected keyboard, falling
  // back to the built-in one if that's all there is.
  readonly property var primary: {
    var list = snapshot.keyboards || []
    for (var i = 0; i < list.length; i++) {
      if (list[i].integration !== "internal") return list[i]
    }
    return list.length > 0 ? list[0] : null
  }

  function refresh() {
    if (collector.running) return
    collector.running = true
  }

  Process {
    id: collector
    command: ["/usr/bin/python3", root.helperPath]
    stdout: StdioCollector { id: output; waitForEnd: true }
    stderr: StdioCollector { id: errors; waitForEnd: true }
    onRunningChanged: if (running) collectorWatchdog.restart(); else collectorWatchdog.stop()
    onExited: function(exitCode) {
      if (exitCode !== 0) {
        root.error = errors.text.trim() || "Keyboard detection failed."
        return
      }
      try {
        var value = JSON.parse(output.text)
        if (value.schemaVersion !== 1 || !Array.isArray(value.keyboards))
          throw new Error("Unrecognized detector response")
        root.snapshot = value
        root.error = value.error || ""
      } catch (exception) {
        root.error = String(exception)
      }
    }
  }
  // detect_devices.py bounds its own subprocess calls internally (3s), but
  // a watchdog on the outer process is cheap insurance against it hanging
  // before ever reaching one — an unbounded StdioCollector read otherwise
  // has no ceiling of its own.
  Timer { id: collectorWatchdog; interval: 10000; onTriggered: if (collector.running) collector.running = false }

  // Hotplugging a keyboard doesn't change any *file* the shell watches, so
  // poll on an interval rather than relying on inotify. 4s keeps a freshly
  // plugged-in board showing up quickly without hammering udevadm.
  Timer { interval: 4000; running: true; repeat: true; onTriggered: root.refresh() }

  // Long-running keypress counter for the heatmap. Started once and kept
  // alive for the life of the shell session; restarts itself if it ever
  // exits (it shouldn't under normal operation — evdev read errors on one
  // device are handled internally without exiting).
  Process {
    id: heatmapDaemon
    command: ["/usr/bin/python3", root.heatmapDaemonPath]
    stderr: SplitParser {
      onRead: function(line) {
        if (line.indexOf("watching ") === 0 || line.indexOf("] watching ") !== -1) root.heatmapWatchingAnyDevice = true
        if (line.indexOf("cannot open") !== -1 && line.indexOf("Permission denied") !== -1) root.heatmapPermissionIssue = true
      }
    }
    onExited: heatmapDaemonRestart.restart()
  }
  Timer { id: heatmapDaemonRestart; interval: 3000; onTriggered: heatmapDaemon.running = true }

  Component.onCompleted: {
    refresh()
    heatmapDaemon.running = true
  }
}
