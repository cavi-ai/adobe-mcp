// Fixed Character Animator Accessibility operations. Project ownership and artifact receipts
// are enforced by the supervising transport; native command acknowledgment is not completion.
import AppKit
import ApplicationServices
import CoreGraphics
import CryptoKit
import Darwin
import Foundation
import ImageIO
import ScreenCaptureKit
import Vision

let characterBundleID = "com.adobe.Character-Animator.application"
let defaultApplication = "/Applications/Adobe Character Animator 2026/Adobe Character Animator 2026.app"
let commandPaths: [String: [String]] = [
    "open_project": ["File", "Open Project..."],
    "import": ["File", "Import..."],
    "save": ["File", "Save Project (always saved) "],
    "save_as": ["File", "Save Project As..."],
    "export_png_wav": ["File", "Export", "PNG Sequence and WAV..."],
    "compute_lip_sync": ["Timeline", "Compute Lip Sync Take from Audio and Transcript"],
    "add_to_new_scene": ["Scene", "Add to New Scene"],
    "create_take_from_scene_audio": ["Timeline", "Compute Lip Sync Take from Scene Audio"]
]

struct NativeFailure: Error {
    let code: String
    let message: String
}
struct ProjectHandleFailure: Error {
    let code: String
    let message: String
    let terminationStatus: Int32?
    let outputBytes: Int
    let timedOut: Bool
}
func fail(_ code: String, _ message: String) throws -> Never { throw NativeFailure(code: code, message: message) }
func sha(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
func canonical(_ value: Any) throws -> Data { try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .fragmentsAllowed]) }
func fingerprintValue(_ value: Any, inImportChooser: Bool = false) -> Any {
    if var node = value as? [String: Any] {
        let importChooser = inImportChooser || node["identifier"] as? String == "open-panel"
        // Directory listings can change when the operation journal is saved.
        // The import binds its exact staged URL, hash and selected file separately.
        if importChooser, node["role"] as? String == "AXBrowser" { node.removeValue(forKey: "children") }
        // Cocoa remeasures file-name labels while idle. URL, value, selection,
        // origin and height remain pinned; editable fields and windows retain all geometry.
        if node["role"] as? String == "AXTextField", node["identifier"] == nil,
           (node["url"] as? String)?.hasPrefix("file:") == true,
           var f = node["frame"] as? [Double], f.count == 4 {
            f[2] = 0; node["frame"] = f
        }
        for key in node.keys { node[key] = fingerprintValue(node[key]!, inImportChooser: importChooser) }
        return node
    }
    if let values = value as? [Any] { return values.map { fingerprintValue($0, inImportChooser: inImportChooser) } }
    return value
}
func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
}
func axElement(_ value: CFTypeRef?) -> AXUIElement? {
    guard let value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
    return unsafeBitCast(value, to: AXUIElement.self)
}
func text(_ element: AXUIElement, _ name: String) -> String? {
    if let string = attribute(element, name) as? String { return String(string.prefix(4096)) }
    if let url = attribute(element, name) as? URL { return url.absoluteString }
    return nil
}
func bool(_ element: AXUIElement, _ name: String) -> Bool? { (attribute(element, name) as? NSNumber)?.boolValue }
func children(_ element: AXUIElement) -> [AXUIElement] { attribute(element, "AXChildren") as? [AXUIElement] ?? [] }
func descendants(_ root: AXUIElement, limit: Int = 3000) -> [AXUIElement] {
    var pending = children(root), result: [AXUIElement] = []
    while !pending.isEmpty && result.count < limit {
        let item = pending.removeLast()
        result.append(item)
        pending.append(contentsOf: children(item))
    }
    return result
}
func actions(_ element: AXUIElement) -> [String] {
    var value: CFArray?
    return AXUIElementCopyActionNames(element, &value) == .success ? value as? [String] ?? [] : []
}
func frame(_ element: AXUIElement) -> [Double]? {
    guard let position = attribute(element, "AXPosition"), let size = attribute(element, "AXSize"),
          CFGetTypeID(position) == AXValueGetTypeID(), CFGetTypeID(size) == AXValueGetTypeID() else { return nil }
    var p = CGPoint.zero, s = CGSize.zero
    guard AXValueGetValue(unsafeBitCast(position, to: AXValue.self), .cgPoint, &p),
          AXValueGetValue(unsafeBitCast(size, to: AXValue.self), .cgSize, &s) else { return nil }
    return [p.x, p.y, s.width, s.height]
}
func scalarNode(_ element: AXUIElement, path: String) -> [String: Any] {
    var node: [String: Any] = ["path": path, "role": text(element, "AXRole") ?? "unknown", "actions": actions(element)]
    for (key, field) in [("AXSubrole", "subrole"), ("AXTitle", "title"), ("AXDescription", "description"),
                         ("AXDocument", "document_url"), ("AXIdentifier", "identifier"), ("AXURL", "url")] {
        if let value = text(element, key) { node[field] = value }
    }
    if node["role"] as? String != "AXSecureTextField", let value = text(element, "AXValue") { node["value"] = value }
    for (key, field) in [("AXEnabled", "enabled"), ("AXSelected", "selected"), ("AXModal", "modal")] {
        if let value = bool(element, key) { node[field] = value }
    }
    if let value = frame(element) { node["frame"] = value }
    return node
}

final class Observation {
    var state: [String: Any]
    let application: NSRunningApplication?
    let root: AXUIElement?
    var menuElements: [String: AXUIElement] = [:]
    var nodes: [(AXUIElement, [String: Any])] = []
    var focusedWindow: AXUIElement?
    init(state: [String: Any], application: NSRunningApplication?, root: AXUIElement?) {
        self.state = state; self.application = application; self.root = root
    }
}

@MainActor final class CharacterNative {
    let applicationURL: URL
    var dispatched = false
    var primaryCommandDispatched = false
    var phase = "request_validation"
    var lastState: [String: Any]?
    var submissionMayHaveStarted = false
    var continuationDiagnostics: [String: Any]?
    init(applicationPath: String) throws {
        applicationURL = URL(fileURLWithPath: applicationPath).standardizedFileURL.resolvingSymlinksInPath()
        guard applicationURL.pathExtension == "app", let bundle = Bundle(url: applicationURL),
              bundle.bundleIdentifier == characterBundleID else { try fail("APPLICATION_MISMATCH", "Application bundle identity does not match Character Animator") }
    }
    func projectHandles(_ pid: pid_t) throws -> [String] {
        // This fixed read-only process inspects only the target app's open project-file paths.
        let process = Process(), pipe = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
        process.arguments = ["-n", "-P", "-a", "-p", String(pid), "-Fn"]
        process.standardOutput = pipe; process.standardError = FileHandle.nullDevice
        do { try process.run() }
        catch { throw ProjectHandleFailure(code: "launch_failed", message: "lsof could not start",
                                           terminationStatus: nil, outputBytes: 0, timedOut: false) }
        let timeoutLock = NSLock()
        var didTimeout = false
        DispatchQueue.global().asyncAfter(deadline: .now() + 5) {
            if process.isRunning {
                timeoutLock.lock(); didTimeout = true; timeoutLock.unlock()
                process.terminate()
            }
        }
        let output: Data
        do { output = try pipe.fileHandleForReading.read(upToCount: 2 * 1024 * 1024 + 1) ?? Data() }
        catch {
            if process.isRunning { process.terminate() }
            timeoutLock.lock(); let timedOut = didTimeout; timeoutLock.unlock()
            throw ProjectHandleFailure(code: "read_failed", message: "lsof output could not be read",
                                       terminationStatus: nil, outputBytes: 0,
                                       timedOut: timedOut)
        }
        if output.count > 2 * 1024 * 1024 {
            if process.isRunning { process.terminate() }
            timeoutLock.lock(); let timedOut = didTimeout; timeoutLock.unlock()
            throw ProjectHandleFailure(code: "output_limit", message: "lsof output exceeded its limit",
                                       terminationStatus: nil, outputBytes: output.count,
                                       timedOut: timedOut)
        }
        process.waitUntilExit()
        timeoutLock.lock(); let timedOut = didTimeout; timeoutLock.unlock()
        guard process.terminationStatus == 0 else {
            throw ProjectHandleFailure(code: timedOut ? "timed_out" : "nonzero_exit",
                                       message: timedOut ? "lsof exceeded its inspection time limit" : "lsof exited unsuccessfully",
                                       terminationStatus: process.terminationStatus, outputBytes: output.count,
                                       timedOut: timedOut)
        }
        return Array(Set(String(decoding: output, as: UTF8.self).split(separator: "\n").compactMap { line -> String? in
            guard line.first == "n" else { return nil }
            let path = String(line.dropFirst())
            return path.hasSuffix(".chproj") || path.contains("/Ch Data/") ? path : nil
        })).sorted()
    }
    func menuItem(_ root: AXUIElement, path: [String]) -> AXUIElement? {
        guard let bar = axElement(attribute(root, "AXMenuBar")), let first = path.first,
              var current = children(bar).first(where: { text($0, "AXTitle") == first }) else { return nil }
        for component in path.dropFirst() {
            let menus = children(current).filter { text($0, "AXRole") == "AXMenu" }
            let candidates = (menus.isEmpty ? children(current) : menus.flatMap(children)).filter { text($0, "AXTitle") == component }
            guard candidates.count == 1 else { return nil }
            current = candidates[0]
        }
        return current
    }
    func observe(full: Bool = true) throws -> Observation {
        let bundleMatches = NSRunningApplication.runningApplications(withBundleIdentifier: characterBundleID)
        let matches = bundleMatches
            .filter { $0.bundleURL?.standardizedFileURL.resolvingSymlinksInPath() == applicationURL }
        var state: [String: Any] = ["bundle_id": characterBundleID, "application": applicationURL.path,
            "app_version": Bundle(url: applicationURL)?.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "unknown",
            "accessibility_trusted": AXIsProcessTrusted(), "post_events_authorized": CGPreflightPostEventAccess(),
            "screen_capture_authorized": CGPreflightScreenCaptureAccess(),
            "running": !matches.isEmpty, "process_count": matches.count,
            "bundle_process_count": bundleMatches.count, "launches_application": false]
        guard matches.count <= 1 else { try fail("AMBIGUOUS_PROCESS", "Multiple Character Animator instances are running") }
        guard let application = matches.first else {
            state["fingerprint"] = sha(try canonical(state)); lastState = state
            return Observation(state: state, application: nil, root: nil)
        }
        state["pid"] = Int(application.processIdentifier)
        state["launch_time"] = application.launchDate?.timeIntervalSince1970 ?? 0
        state["frontmost"] = application.isActive
        state["hidden"] = application.isHidden
        let root = AXUIElementCreateApplication(application.processIdentifier)
        AXUIElementSetMessagingTimeout(root, 0.75)
        let observation = Observation(state: state, application: application, root: root)
        if full && AXIsProcessTrusted() {
            var frontmostSettable: DarwinBoolean = false
            let frontmostCapability = AXUIElementIsAttributeSettable(root, "AXFrontmost" as CFString, &frontmostSettable)
            state["focus_control"] = ["attribute": "AXFrontmost", "settable": frontmostCapability == .success && frontmostSettable.boolValue]
            var windows = attribute(root, "AXWindows") as? [AXUIElement] ?? []
            if let focused = axElement(attribute(root, "AXFocusedWindow")) {
                observation.focusedWindow = focused
                if !windows.contains(where: { CFEqual($0, focused) }) { windows.append(focused) }
            }
            // Adobe can report its canvas as focused while a Cocoa file chooser is modal.
            // Read-only hit testing of observed app window centers recovers those AX windows.
            let serverWindows = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
            for serverWindow in serverWindows where serverWindow[kCGWindowOwnerPID as String] as? Int == Int(application.processIdentifier) {
                guard serverWindow[kCGWindowIsOnscreen as String] as? Bool == true,
                      let bounds = serverWindow[kCGWindowBounds as String] as? [String: Any],
                      let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary), rect.width > 300, rect.height > 100 else { continue }
                var hit: AXUIElement?
                guard AXUIElementCopyElementAtPosition(root, Float(rect.midX), Float(rect.midY), &hit) == .success else { continue }
                for _ in 0..<16 {
                    guard let candidate = hit else { break }
                    var candidatePID: pid_t = 0
                    guard AXUIElementGetPid(candidate, &candidatePID) == .success, candidatePID == application.processIdentifier else { break }
                    if text(candidate, "AXRole") == "AXWindow" {
                        if !windows.contains(where: { CFEqual($0, candidate) }) { windows.append(candidate) }
                        if bool(candidate, "AXModal") == true { observation.focusedWindow = candidate }
                        break
                    }
                    hit = axElement(attribute(candidate, "AXParent"))
                }
            }
            // The focused text field identifies its owning sheet more precisely than
            // a hit-tested modal parent, which may still contain an active Go To sheet.
            var focusedElement = axElement(attribute(root, "AXFocusedUIElement"))
            for _ in 0..<16 {
                guard let candidate = focusedElement else { break }
                var candidatePID: pid_t = 0
                guard AXUIElementGetPid(candidate, &candidatePID) == .success,
                      candidatePID == application.processIdentifier else { break }
                let visible = frame(candidate).map { f in serverWindows.contains { item in
                    guard item[kCGWindowOwnerPID as String] as? Int == Int(application.processIdentifier),
                          item[kCGWindowIsOnscreen as String] as? Bool == true,
                          let bounds = item[kCGWindowBounds as String] as? [String: Any],
                          let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary) else { return false }
                    return abs(rect.minX - f[0]) <= 2 && abs(rect.minY - f[1]) <= 2
                        && abs(rect.width - f[2]) <= 2 && abs(rect.height - f[3]) <= 2
                }} ?? false
                if visible && ["AXSheet", "AXWindow"].contains(text(candidate, "AXRole") ?? "") {
                    observation.focusedWindow = candidate
                    if !windows.contains(where: { CFEqual($0, candidate) }) { windows.append(candidate) }
                    break
                }
                focusedElement = axElement(attribute(candidate, "AXParent"))
            }
            var budget = 512, truncated = false
            func walk(_ element: AXUIElement, depth: Int, path: String) -> [String: Any] {
                var node = scalarNode(element, path: path)
                observation.nodes.append((element, node)); budget -= 1
                // File chooser sidebars are unrelated to project/scene identity and include account locations.
                let sidebar = node["role"] as? String == "AXOutline" && node["description"] as? String == "sidebar"
                if depth < 12 && budget > 0 && !sidebar {
                    var descendants: [[String: Any]] = []
                    for (index, child) in children(element).prefix(128).enumerated() {
                        if budget <= 0 { truncated = true; break }
                        descendants.append(walk(child, depth: depth + 1, path: path + "/" + String(index)))
                    }
                    if !descendants.isEmpty { node["children"] = descendants }
                } else if !children(element).isEmpty && !sidebar { truncated = true }
                return node
            }
            state["windows"] = windows.prefix(8).enumerated().map { walk($0.element, depth: 0, path: "window/" + String($0.offset)) }
            state["tree_truncated"] = truncated || windows.count > 8
            if let focused = observation.focusedWindow { state["focused_window"] = scalarNode(focused, path: "focused") }
            state["selected_elements"] = observation.nodes.map(\.1).filter { $0["selected"] as? Bool == true }
            var menus: [String: Any] = [:]
            for (operation, path) in commandPaths {
                if let element = menuItem(root, path: path) {
                    observation.menuElements[operation] = element
                    menus[operation] = ["path": path, "available": true, "enabled": bool(element, "AXEnabled") ?? false]
                } else { menus[operation] = ["path": path, "available": false, "enabled": false] }
            }
            state["menu_commands"] = menus
            if let bar = axElement(attribute(root, "AXMenuBar")) {
                var remaining = 256
                func menuEntries(_ element: AXUIElement, depth: Int) -> [[String: Any]] {
                    guard depth < 8, remaining > 0 else { return [] }
                    var entries: [[String: Any]] = []
                    for child in children(element).prefix(64) {
                        guard remaining > 0 else { break }
                        remaining -= 1
                        let role = text(child, "AXRole") ?? ""
                        if role == "AXMenuItem" {
                            var entry: [String: Any] = ["title": text(child, "AXTitle") ?? "",
                                                        "enabled": bool(child, "AXEnabled") ?? false]
                            if let identifier = text(child, "AXIdentifier") { entry["identifier"] = identifier }
                            entry["children"] = menuEntries(child, depth: depth + 1)
                            entries.append(entry)
                        } else {
                            entries.append(contentsOf: menuEntries(child, depth: depth + 1))
                        }
                    }
                    return entries
                }
                state["menu_inventory"] = ["File", "Scene", "Timeline"].map { title -> [String: Any] in
                    let roots = children(bar).filter { text($0, "AXRole") == "AXMenuBarItem" && text($0, "AXTitle") == title }
                    return ["title": title, "available": roots.count == 1,
                            "items": roots.count == 1 ? menuEntries(roots[0], depth: 0) : []]
                }
                state["menu_inventory_truncated"] = remaining == 0
            }
            do {
                let handles = try projectHandles(application.processIdentifier)
                state["project_file_handles"] = handles
                state["project_roots"] = Array(Set(handles.compactMap { path -> String? in
                    if let range = path.range(of: "/Ch Data/") { return String(path[..<range.lowerBound]) }
                    return path.hasSuffix(".chproj") ? URL(fileURLWithPath: path).deletingLastPathComponent().path : nil
                })).sorted()
                state["project_documents"] = handles.filter { $0.hasSuffix(".chproj") }
            } catch {
                state["project_roots"] = []
                state["project_handle_error"] = "unavailable"
                if let failure = error as? ProjectHandleFailure {
                    var details: [String: Any] = ["code": failure.code, "message": failure.message,
                                                   "output_bytes": failure.outputBytes,
                                                   "timed_out": failure.timedOut]
                    if let status = failure.terminationStatus { details["termination_status"] = status }
                    state["project_handle_failure"] = details
                } else if let failure = error as? NativeFailure {
                    state["project_handle_failure"] = ["code": failure.code, "message": failure.message]
                } else {
                    state["project_handle_failure"] = ["code": "inspection_failed", "message": "Project handle inspection failed"]
                }
            }
            let documents = observation.nodes.compactMap { $0.1["document_url"] as? String }
            state["document_urls"] = Array(Set(documents)).sorted()
            state["identity_limitations"] = documents.isEmpty ? ["No native AXDocument URL is exposed; window titles are not file paths", "Project roots are observed open-file-handle evidence, not a native scene identifier"] : []
            let prompts = observation.nodes.map(\.1).filter { node in
                let role = node["role"] as? String ?? ""
                guard ["AXWindow", "AXSheet", "AXStaticText", "AXButton", "AXSecureTextField"].contains(role) else { return false }
                let value = ((node["title"] as? String ?? "") + " " + (node["value"] as? String ?? "")).lowercased()
                return role == "AXSecureTextField" || ["sign in", "log in", "apple id", "apple account", "verification code", "license expired", "activate your", "two-factor", "allow access"].contains(where: value.contains)
            }
            state["authentication_or_permission_prompt"] = !prompts.isEmpty
            if !prompts.isEmpty { state["blocked_prompt"] = "Resolve the application prompt manually; this adapter will not authenticate or grant permissions" }
        }
        state["fingerprint"] = sha(try canonical(fingerprintValue(state))); observation.state = state; lastState = state
        return observation
    }
    func expected(_ request: [String: Any], observation: Observation) throws {
        guard observation.application != nil, observation.state["accessibility_trusted"] as? Bool == true else { try fail("APPLICATION_UNAVAILABLE", "An already running app and existing Accessibility permission are required") }
        guard observation.state["authentication_or_permission_prompt"] as? Bool != true else { try fail("AUTHENTICATION_BOUNDARY", "Application authentication or permission prompt requires manual handling") }
        guard let expected = request["expected"] as? [String: Any],
              expected["pid"] as? Int == observation.state["pid"] as? Int,
              expected["launch_time"] as? Double == observation.state["launch_time"] as? Double,
              expected["fingerprint"] as? String == observation.state["fingerprint"] as? String else { try fail("STALE_STATE", "Inspect again; process, window or precondition fingerprint differs") }
    }
    func expectedFocusIdentity(_ request: [String: Any], observation: Observation) throws {
        guard let app = observation.application,
              observation.state["accessibility_trusted"] as? Bool == true else {
            try fail("APPLICATION_UNAVAILABLE", "An already running app and existing Accessibility permission are required")
        }
        guard observation.state["authentication_or_permission_prompt"] as? Bool != true else {
            try fail("AUTHENTICATION_BOUNDARY", "Application authentication or permission prompt requires manual handling")
        }
        guard let expected = request["expected"] as? [String: Any],
              expected["bundle_id"] as? String == characterBundleID,
              observation.state["bundle_id"] as? String == characterBundleID,
              expected["application"] as? String == applicationURL.path,
              observation.state["application"] as? String == applicationURL.path,
              expected["pid"] as? Int == Int(app.processIdentifier),
              observation.state["pid"] as? Int == Int(app.processIdentifier),
              expected["launch_time"] as? Double == observation.state["launch_time"] as? Double,
              expected["project_roots"] as? [String] == observation.state["project_roots"] as? [String] else {
            try fail("STALE_STATE", "The observed application, process, launch or project identity changed before activation")
        }
    }
    func ownedRoot(_ parameters: [String: Any], _ observation: Observation) throws {
        guard let requested = parameters["project_root"] as? String, requested.hasPrefix("/"),
              let roots = observation.state["project_roots"] as? [String], roots == [requested] else {
            try fail("PROJECT_IDENTITY_UNAVAILABLE", "An explicit owned project root must match the sole native project-data handle root")
        }
    }
    func openPreparedCopy(_ parameters: [String: Any], _ observation: Observation) async throws -> [String: Any] {
        guard observation.application == nil,
              observation.state["running"] as? Bool == false,
              observation.state["process_count"] as? Int == 0,
              observation.state["accessibility_trusted"] as? Bool == true,
              observation.state["authentication_or_permission_prompt"] as? Bool != true,
              let preparedID = parameters["prepared_copy_id"] as? String,
              preparedID.range(of: "^[0-9a-f]{32}$", options: .regularExpression) != nil,
              let rootString = parameters["project_root"] as? String,
              let projectString = parameters["project_file"] as? String,
              let identity = parameters["destination_identity"] as? [String: Any],
              let expectedDevice = (identity["device"] as? NSNumber)?.int64Value,
              let expectedInode = (identity["inode"] as? NSNumber)?.int64Value else {
            try fail("INVALID_ARGUMENT", "Open requires one verified prepared project while Character Animator is stopped")
        }
        let root = URL(fileURLWithPath: rootString).standardizedFileURL.resolvingSymlinksInPath()
        let project = URL(fileURLWithPath: projectString).standardizedFileURL.resolvingSymlinksInPath()
        let chData = root.appendingPathComponent("Ch Data", isDirectory: true)
        guard root.path == rootString, project.path == projectString,
              project.deletingLastPathComponent() == root,
              project.pathExtension == "chproj",
              let chDataAttributes = try? FileManager.default.attributesOfItem(atPath: chData.path),
              chDataAttributes[.type] as? FileAttributeType == .typeDirectory,
              (try? chData.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) != true,
              let rootAttributes = try? FileManager.default.attributesOfItem(atPath: root.path),
              rootAttributes[.type] as? FileAttributeType == .typeDirectory,
              rootAttributes[.systemNumber] as? NSNumber == NSNumber(value: expectedDevice),
              rootAttributes[.systemFileNumber] as? NSNumber == NSNumber(value: expectedInode),
              let projectAttributes = try? FileManager.default.attributesOfItem(atPath: project.path),
              projectAttributes[.type] as? FileAttributeType == .typeRegular,
              (try? project.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) != true else {
            try fail("PREPARED_COPY_MISMATCH", "The prepared project file or recorded root identity changed")
        }
        phase = "prepared_copy_open_dispatch"
        dispatched = true
        primaryCommandDispatched = true
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        let opened = try await NSWorkspace.shared.open([project], withApplicationAt: applicationURL,
                                                       configuration: configuration)
        guard opened.bundleIdentifier == characterBundleID,
              opened.bundleURL?.standardizedFileURL.resolvingSymlinksInPath() == applicationURL,
              opened.processIdentifier > 0 else {
            try fail("OPEN_IDENTITY_MISMATCH", "The prepared project did not open in the verified Character Animator bundle")
        }
        let after = try observe()
        return ["state": after.state,
                "result": ["status": "native_prepared_copy_open_requested", "completion": "unverified",
                           "prepared_copy_id": preparedID, "project_root": root.path,
                           "process_id": Int(opened.processIdentifier),
                           "launch_time": opened.launchDate?.timeIntervalSince1970 ?? 0]]
    }
    func perform(_ element: AXUIElement, action: String = "AXPress") throws {
        guard actions(element).contains(action), bool(element, "AXEnabled") != false else { try fail("CONTROL_UNAVAILABLE", "Observed native control does not support this enabled action") }
        dispatched = true
        if phase == "menu_dispatch" { primaryCommandDispatched = true }
        let error = AXUIElementPerformAction(element, action as CFString)
        guard error == .success else { try fail("NATIVE_ACTION_FAILED", "Native action acknowledgment failed (\(error.rawValue)); inspect before any further mutation") }
    }
    func panel(_ observation: Observation, kind: String? = nil) throws -> AXUIElement {
        guard let focused = observation.focusedWindow, bool(focused, "AXModal") == true,
              ["open-panel", "save-panel"].contains(text(focused, "AXIdentifier") ?? "") else { try fail("CHOOSER_UNAVAILABLE", "Expected an observed native open/save panel") }
        if let kind {
            let titles = ["import": "Import", "save_as": "Save Project As", "export_png_wav": "Export Scene", "open_project": "Open Project"]
            guard text(focused, "AXTitle") == titles[kind] else { try fail("CHOOSER_MISMATCH", "Chooser title differs from the requested domain operation") }
        }
        return focused
    }
    func typedChooserIsFocused(_ observation: Observation, kind: String) -> Bool {
        let titles = ["save_as": "Save Project As", "import": "Import",
                      "export_png_wav": "Export Scene", "open_project": "Open Project"]
        guard let title = titles[kind] else { return false }
        let panelID = kind == "import" || kind == "open_project" ? "open-panel" : "save-panel"
        guard let focused = observation.focusedWindow else { return false }
        if text(focused, "AXIdentifier") == panelID && text(focused, "AXTitle") == title
            && bool(focused, "AXModal") == true { return true }
        return text(focused, "AXIdentifier") == "GoToWindow" && text(focused, "AXRole") == "AXSheet"
            && observation.nodes.filter {
                text($0.0, "AXIdentifier") == panelID && text($0.0, "AXTitle") == title
            }.count == 1
    }
    func typedChooserIsObserved(_ observation: Observation, kind: String) -> Bool {
        let titles = ["save_as": "Save Project As", "import": "Import",
                      "export_png_wav": "Export Scene", "open_project": "Open Project"]
        guard let title = titles[kind] else { return false }
        let panelID = kind == "import" || kind == "open_project" ? "open-panel" : "save-panel"
        let panels = observation.nodes.filter {
            text($0.0, "AXIdentifier") == panelID && text($0.0, "AXTitle") == title
                && bool($0.0, "AXModal") == true
        }
        guard panels.count == 1 else { return safeBackgroundMain(observation) }
        return typedChooserIsFocused(observation, kind: kind)
            || hasOnlyExpectedChooserModal(observation, kind: kind)
    }
    func safeBackgroundMain(_ observation: Observation) -> Bool {
        guard observation.state["frontmost"] as? Bool == false,
              hasNoUnrelatedModal(observation) else { return false }
        let canvases = observation.nodes.filter { element, _ in
            text(element, "AXRole") == "AXLayoutArea"
                && text(element, "AXTitle") != nil && frame(element) != nil
                && bool(element, "AXModal") != true
        }
        guard canvases.count == 1 else { return false }
        if let focused = observation.focusedWindow {
            return CFEqual(focused, canvases[0].0)
        }
        return true
    }
    func hasNoUnrelatedModal(_ observation: Observation) -> Bool {
        !observation.nodes.contains { element, _ in
            bool(element, "AXModal") == true || text(element, "AXRole") == "AXSheet"
        }
    }
    func hasOnlyExpectedChooserModal(_ observation: Observation, kind: String) -> Bool {
        let titles = ["save_as": "Save Project As", "import": "Import",
                      "export_png_wav": "Export Scene", "open_project": "Open Project"]
        guard let title = titles[kind] else { return false }
        let panelID = kind == "import" || kind == "open_project" ? "open-panel" : "save-panel"
        let panels = observation.nodes.filter {
            text($0.0, "AXIdentifier") == panelID && text($0.0, "AXTitle") == title
                && bool($0.0, "AXModal") == true
        }
        guard panels.count == 1 else { return false }
        let panel = panels[0].0
        return observation.nodes.allSatisfy { element, _ in
            let role = text(element, "AXRole")
            guard bool(element, "AXModal") == true || role == "AXSheet" else { return true }
            if CFEqual(element, panel) { return true }
            guard role == "AXSheet", text(element, "AXIdentifier") == "GoToWindow" else { return false }
            var parent = axElement(attribute(element, "AXParent"))
            for _ in 0..<16 {
                guard let current = parent else { return false }
                if CFEqual(current, panel) { return true }
                parent = axElement(attribute(current, "AXParent"))
            }
            return false
        }
    }
    func node(_ observation: Observation, identifier: String, role: String? = nil) throws -> AXUIElement {
        let candidates = observation.nodes.filter { text($0.0, "AXIdentifier") == identifier && (role == nil || text($0.0, "AXRole") == role) }
        guard candidates.count == 1 else { try fail("AMBIGUOUS_CONTROL", "Required native control was absent or ambiguous") }
        return candidates[0].0
    }
    func setName(_ observation: Observation, name: String) throws {
        guard !name.isEmpty, name.count <= 128, !name.contains("/"), !name.contains(":"), !name.unicodeScalars.contains(where: { $0.value < 32 }), name != ".", name != ".." else { try fail("INVALID_ARGUMENT", "Chooser name must be a printable basename") }
        let field = try node(observation, identifier: "saveAsNameTextField", role: "AXTextField")
        var settable: DarwinBoolean = false
        guard AXUIElementIsAttributeSettable(field, "AXValue" as CFString, &settable) == .success && settable.boolValue else { try fail("CONTROL_UNAVAILABLE", "Native filename field is not settable") }
        dispatched = true
        guard AXUIElementSetAttributeValue(field, "AXValue" as CFString, name as CFString) == .success else { try fail("NATIVE_ACTION_FAILED", "Filename update outcome is unknown") }
    }
    func chooserKey(_ observation: Observation, key: CGKeyCode, flags: CGEventFlags = []) throws {
        guard CGPreflightPostEventAccess(), let app = observation.application,
              let window = observation.focusedWindow, let f = frame(window),
              observation.state["authentication_or_permission_prompt"] as? Bool != true,
              NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier else {
            try fail("FOCUS_CHANGED", "Chooser input requires existing event access and the observed foreground application")
        }
        let expectedFrame = CGRect(x: f[0], y: f[1], width: f[2], height: f[3])
        let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
        guard let top = windows.first(where: { item in
                  // The system cursor is a visual plane, not a keyboard-input window.
                  if item[kCGWindowOwnerName as String] as? String == "Window Server",
                     item[kCGWindowLayer as String] as? Int == Int(CGWindowLevelForKey(.cursorWindow)) { return false }
                  guard (item[kCGWindowAlpha as String] as? Double ?? 1) > 0,
                        let bounds = item[kCGWindowBounds as String] as? [String: Any],
                        let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary) else { return false }
                  return rect.intersects(expectedFrame)
              }), top[kCGWindowOwnerPID as String] as? Int == Int(app.processIdentifier),
              let bounds = top[kCGWindowBounds as String] as? [String: Any],
              let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary),
              abs(rect.minX - f[0]) <= 2, abs(rect.minY - f[1]) <= 2,
              abs(rect.width - f[2]) <= 2, abs(rect.height - f[3]) <= 2,
              let down = CGEvent(keyboardEventSource: nil, virtualKey: key, keyDown: true),
              let up = CGEvent(keyboardEventSource: nil, virtualKey: key, keyDown: false) else {
            try fail("FOCUS_CHANGED", "The exact observed chooser must be the topmost native window")
        }
        down.flags = flags; up.flags = flags
        dispatched = true
        down.post(tap: .cghidEventTap); up.post(tap: .cghidEventTap)
    }
    func capture(_ observation: Observation, parameters: [String: Any]) async throws -> [String: Any] {
        guard CGPreflightScreenCaptureAccess() else { try fail("SCREEN_PERMISSION_UNAVAILABLE", "Screen capture permission is absent; no permission request was made") }
        guard let app = observation.application else { try fail("APPLICATION_UNAVAILABLE", "Character Animator is not running") }
        let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false)
        let candidates = content.windows.filter { $0.owningApplication?.processID == app.processIdentifier && $0.frame.width > 300 && $0.frame.height > 100 }
        let selected: SCWindow?
        if let id = parameters["window_id"] as? UInt32 { selected = candidates.first { $0.windowID == id } }
        else if let focused = observation.focusedWindow, let f = frame(focused) {
            selected = candidates.first { abs($0.frame.minX - f[0]) < 3 && abs($0.frame.minY - f[1]) < 3 && abs($0.frame.width - f[2]) < 3 && abs($0.frame.height - f[3]) < 3 }
                ?? candidates.max { $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height }
        } else { selected = candidates.max { $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height } }
        guard let window = selected else { try fail("WINDOW_UNAVAILABLE", "No Character Animator window is available for capture") }
        let configuration = SCStreamConfiguration()
        configuration.width = min(Int(window.frame.width * 2), 8192)
        configuration.height = min(Int(window.frame.height * 2), 8192)
        configuration.showsCursor = false
        let image = try await SCScreenshotManager.captureImage(contentFilter: SCContentFilter(desktopIndependentWindow: window), configuration: configuration)
        let mutable = NSMutableData()
        guard let encoder = CGImageDestinationCreateWithData(mutable, "public.png" as CFString, 1, nil) else { try fail("CAPTURE_FAILED", "PNG encoder is unavailable") }
        CGImageDestinationAddImage(encoder, image, nil)
        guard CGImageDestinationFinalize(encoder) else { try fail("CAPTURE_FAILED", "PNG encoding failed") }
        let data = mutable as Data
        guard data.count <= 32 * 1024 * 1024 else { try fail("CAPTURE_LIMIT", "Window capture exceeds 32 MiB") }
        let recognition = VNRecognizeTextRequest(); recognition.recognitionLevel = .accurate; recognition.usesLanguageCorrection = false
        try VNImageRequestHandler(cgImage: image, options: [:]).perform([recognition])
        let tokens: [[String: Any]] = (recognition.results ?? []).prefix(512).compactMap { item in
            guard let candidate = item.topCandidates(1).first else { return nil }
            return ["text": String(candidate.string.prefix(512)), "confidence": Double(candidate.confidence),
                "bounds": [item.boundingBox.minX, item.boundingBox.minY, item.boundingBox.width, item.boundingBox.height].map(Double.init)]
        }
        var result: [String: Any] = ["window_id": window.windowID, "window_title": window.title ?? "",
            "frame": [window.frame.minX, window.frame.minY, window.frame.width, window.frame.height].map(Double.init),
            "sha256": sha(data), "bytes": data.count, "width": image.width, "height": image.height,
            "ocr": tokens, "ocr_identity": "observed_text_and_geometry_not_native_scene_id"]
        if let destination = parameters["destination"] as? String {
            let url = URL(fileURLWithPath: destination).standardizedFileURL
            guard destination.hasPrefix("/"), url.path == destination, url.pathExtension.lowercased() == "png",
                  url.resolvingSymlinksInPath() == url, !FileManager.default.fileExists(atPath: destination) else { try fail("INVALID_DESTINATION", "Capture requires a new absolute PNG path without symlinks") }
            dispatched = true; try data.write(to: url, options: [.withoutOverwriting]); result["path"] = destination
        }
        return result
    }
    func focusApplication(_ observation: Observation) async throws {
        guard let root = observation.root, let app = observation.application else { try fail("APPLICATION_UNAVAILABLE", "Character Animator is not running") }
        guard observation.state["authentication_or_permission_prompt"] as? Bool != true else {
            try fail("AUTHENTICATION_BOUNDARY", "Resolve the application prompt manually; no native input was sent")
        }
        if NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier,
           bool(root, "AXFrontmost") == true, let focused = observation.focusedWindow, let f = frame(focused) {
            let visible = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
            if visible.contains(where: { item in
                guard item[kCGWindowOwnerPID as String] as? Int == Int(app.processIdentifier),
                      (item[kCGWindowAlpha as String] as? Double ?? 1) > 0,
                      let bounds = item[kCGWindowBounds as String] as? [String: Any],
                      let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary) else { return false }
                return abs(rect.minX - f[0]) <= 2 && abs(rect.minY - f[1]) <= 2
                    && abs(rect.width - f[2]) <= 2 && abs(rect.height - f[3]) <= 2
            }) { return }
        }
        var settable: DarwinBoolean = false
        guard AXUIElementIsAttributeSettable(root, "AXFrontmost" as CFString, &settable) == .success && settable.boolValue else { try fail("FOCUS_UNAVAILABLE", "Native AXFrontmost control is not settable") }
        phase = "focus_dispatch"; dispatched = true
        guard AXUIElementSetAttributeValue(root, "AXFrontmost" as CFString, kCFBooleanTrue) == .success else { try fail("FOCUS_CHANGED", "Native foreground request was not acknowledged") }
        let docks = NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.dock")
        guard docks.count == 1 else { try fail("FOCUS_UNAVAILABLE", "The Dock process is unavailable or ambiguous") }
        let dockRoot = AXUIElementCreateApplication(docks[0].processIdentifier)
        let dockItems = descendants(dockRoot).filter {
            text($0, "AXSubrole") == "AXApplicationDockItem"
                && (attribute($0, "AXURL") as? URL)?.standardizedFileURL.path == applicationURL.path
        }
        guard dockItems.count == 1, actions(dockItems[0]).contains("AXPress") else {
            try fail("FOCUS_UNAVAILABLE", "The exact installed Character Animator Dock item is absent or ambiguous")
        }
        try perform(dockItems[0])
        _ = app.activate(options: [.activateAllWindows])
        phase = "focus_observation"
        let deadline = ProcessInfo.processInfo.systemUptime + 5.0
        while true {
            guard deadline - ProcessInfo.processInfo.systemUptime > 0 else { break }
            guard AXIsProcessTrusted() else {
                try fail("FOCUS_UNAVAILABLE", "Accessibility permission changed during foreground observation")
            }
            let instances = NSRunningApplication.runningApplications(withBundleIdentifier: characterBundleID)
                .filter { $0.bundleURL?.standardizedFileURL.resolvingSymlinksInPath() == applicationURL }
            guard instances.count == 1,
                  instances[0].processIdentifier == app.processIdentifier,
                  (instances[0].launchDate?.timeIntervalSince1970 ?? 0)
                    == (app.launchDate?.timeIntervalSince1970 ?? 0) else {
                try fail("FOCUS_CHANGED", "Character Animator process identity changed during foreground activation")
            }
            let currentWorkspacePID = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
            let currentAXRoot = AXUIElementCreateApplication(app.processIdentifier)
            let axRemaining = deadline - ProcessInfo.processInfo.systemUptime
            guard axRemaining > 0 else { break }
            AXUIElementSetMessagingTimeout(currentAXRoot, Float(min(0.75, axRemaining)))
            if currentWorkspacePID == app.processIdentifier,
               bool(currentAXRoot, "AXFrontmost") == true { return }
            let sleepRemaining = deadline - ProcessInfo.processInfo.systemUptime
            guard sleepRemaining > 0 else { break }
            try await Task.sleep(nanoseconds: UInt64(min(0.1, sleepRemaining) * 1_000_000_000))
        }
        let system = AXUIElementCreateSystemWide()
        var livePID: pid_t = 0
        if let focusedApp = axElement(attribute(system, "AXFocusedApplication")) { AXUIElementGetPid(focusedApp, &livePID) }
        try fail("FOCUS_CHANGED", "Native foreground PID did not stabilize after the single activation (workspace=\(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0), accessibility=\(livePID), target=\(app.processIdentifier), app_frontmost=\(bool(AXUIElementCreateApplication(app.processIdentifier), "AXFrontmost") ?? false))")
    }
    func chooserDestination(_ initialObservation: Observation, parameters: [String: Any]) async throws -> [String: Any] {
        var observation = initialObservation
        phase = "chooser_preflight"
        guard let kind = parameters["kind"] as? String, ["save_as", "import", "export_png_wav", "open_project"].contains(kind),
              let directory = parameters["directory"] as? String, directory.hasPrefix("/"),
              let basename = parameters["basename"] as? String else { try fail("INVALID_ARGUMENT", "kind, absolute directory and basename are required") }
        let directoryURL = URL(fileURLWithPath: directory).standardizedFileURL
        var isDirectory: ObjCBool = false
        guard directoryURL.path == directory, directoryURL.resolvingSymlinksInPath() == directoryURL,
              !directory.contains("/Mobile Documents/"), !directory.contains("/Library/CloudStorage/"),
              FileManager.default.fileExists(atPath: directory, isDirectory: &isDirectory), isDirectory.boolValue else { try fail("INVALID_DESTINATION", "Use an existing local directory without symlinks") }
        guard !basename.isEmpty, basename.count <= 128, !basename.contains("/"), !basename.contains(":"),
              basename != ".", basename != "..", !basename.unicodeScalars.contains(where: { $0.value < 32 }) else { try fail("INVALID_ARGUMENT", "basename must be a printable file or project name") }
        let destination = directoryURL.appendingPathComponent(basename)
        if kind == "save_as" || kind == "export_png_wav" {
            guard !FileManager.default.fileExists(atPath: destination.path) else { try fail("DESTINATION_EXISTS", "Native save/export destination must be new") }
        } else {
            guard FileManager.default.fileExists(atPath: destination.path) else { try fail("INPUT_NOT_FOUND", "Requested import/project input does not exist") }
        }
        try ownedRoot(parameters, observation)
        guard let app = observation.application else { try fail("APPLICATION_UNAVAILABLE", "Character Animator is not running") }
        let originalPID = app.processIdentifier
        let originalLaunchTime = observation.state["launch_time"] as? String
        try await focusApplication(observation)
        let activated = try observe()
        try ownedRoot(parameters, activated)
        guard activated.application?.processIdentifier == originalPID,
              (activated.state["launch_time"] as? String) == originalLaunchTime,
              activated.state["authentication_or_permission_prompt"] as? Bool != true,
              NSWorkspace.shared.frontmostApplication?.processIdentifier == originalPID,
              typedChooserIsFocused(activated, kind: kind) else {
            try fail("FOCUS_CHANGED", "Activation did not expose the same-process requested chooser without a prompt")
        }
        let originalPanel: AXUIElement
        if let focused = activated.focusedWindow,
           text(focused, "AXIdentifier") == "GoToWindow", text(focused, "AXRole") == "AXSheet" {
            originalPanel = focused
        } else {
            originalPanel = try panel(activated, kind: kind)
        }
        observation = activated
        if kind == "import", observation.nodes.contains(where: {
            $0.1["identifier"] as? String == "where popup"
                && $0.1["value"] as? String == directoryURL.lastPathComponent
                && (($0.1["url"] as? String).flatMap(URL.init(string:)).map({ $0.standardizedFileURL.path == directory }) ?? true)
        }), observation.nodes.contains(where: {
            text($0.0, "AXValue") == basename && bool($0.0, "AXSelected") == true
                && (attribute($0.0, "AXURL") as? URL)?.standardizedFileURL == destination
        }) {
            return try await continueDirectoryEntry(observation, parameters: parameters)
        }
        phase = "chooser_activation"
        phase = "chooser_raise"
        try perform(originalPanel, action: "AXRaise")
        var focusSettable: DarwinBoolean = false
        if AXUIElementIsAttributeSettable(originalPanel, "AXFocused" as CFString, &focusSettable) == .success && focusSettable.boolValue {
            guard AXUIElementSetAttributeValue(originalPanel, "AXFocused" as CFString, kCFBooleanTrue) == .success else { try fail("NATIVE_ACTION_FAILED", "Native chooser focus could not be established") }
        }
        try await Task.sleep(nanoseconds: 120_000_000)
        phase = "chooser_foreground"
        guard NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier else { try fail("FOCUS_CHANGED", "Character Animator is not frontmost; shortcut was not sent") }
        // A single fixed Cocoa chooser shortcut, never a caller-selected key sequence.
        phase = "go_to_folder_shortcut"
        try chooserKey(try observe(), key: 5, flags: [.maskCommand, .maskShift])
        if kind == "import" || kind == "export_png_wav" {
            return ["status": "native_chooser_directory_requested", "directory": directory,
                    "basename": basename, "completion": "unverified", "retry_safe": false]
        }
        phase = "go_to_folder_observation"
        var navigation: Observation?
        try await Task.sleep(nanoseconds: 500_000_000)
        for _ in 0..<100 {
            let current = try observe()
            guard current.state["authentication_or_permission_prompt"] as? Bool != true,
                  NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier else { try fail("AUTHENTICATION_BOUNDARY", "A prompt or foreground application change interrupted directory navigation") }
            if current.focusedWindow.map({ CFEqual($0, originalPanel) }) == true,
               let whereNode = current.nodes.first(where: { $0.1["identifier"] as? String == "where popup" }),
               whereNode.1["value"] as? String == directoryURL.lastPathComponent,
               ((whereNode.1["url"] as? String).flatMap(URL.init(string:)).map({ $0.standardizedFileURL.path == directory }) ?? true) {
                navigation = current; break
            }
            if let focused = current.focusedWindow,
               text(focused, "AXIdentifier") == "GoToWindow", text(focused, "AXRole") == "AXSheet",
               let dialogPath = current.nodes.first(where: { CFEqual($0.0, focused) })?.1["path"] as? String,
               current.nodes.contains(where: {
                   text($0.0, "AXIdentifier") == "PathTextField"
                       && text($0.0, "AXRole") == "AXTextField"
                       && ($0.1["path"] as? String ?? "").hasPrefix(dialogPath + "/")
                       && actions($0.0).contains("AXConfirm")
               }) {
                navigation = current; break
            }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        guard let navigation else { try fail("GO_TO_FOLDER_UNAVAILABLE", "The observed native Go To sheet did not appear; no second shortcut was sent") }
        if navigation.focusedWindow.map({ CFEqual($0, originalPanel) }) == true {
            return ["status": "native_chooser_directory_requested", "directory": directory,
                    "basename": basename, "completion": "unverified", "retry_safe": false]
        }
        guard let dialogPath = navigation.nodes.first(where: {
            text($0.0, "AXIdentifier") == "GoToWindow" && text($0.0, "AXRole") == "AXSheet"
        })?.1["path"] as? String else { try fail("GO_TO_FOLDER_UNAVAILABLE", "The observed Go To sheet became ambiguous") }
        let pathFields = navigation.nodes.filter {
            text($0.0, "AXIdentifier") == "PathTextField" && text($0.0, "AXRole") == "AXTextField"
                && ($0.1["path"] as? String ?? "").hasPrefix(dialogPath + "/")
        }
        guard pathFields.count == 1, actions(pathFields[0].0).contains("AXConfirm") else { try fail("GO_TO_FOLDER_UNAVAILABLE", "The observed native directory field is absent or ambiguous") }
        if kind == "save_as" {
            return ["status": "native_chooser_directory_requested", "directory": directory,
                    "basename": basename, "completion": "unverified", "retry_safe": false]
        }
        phase = "directory_entry"
        var fieldFocusSettable: DarwinBoolean = false
        if AXUIElementIsAttributeSettable(pathFields[0].0, "AXFocused" as CFString, &fieldFocusSettable) == .success,
           fieldFocusSettable.boolValue {
            guard AXUIElementSetAttributeValue(pathFields[0].0, "AXFocused" as CFString, kCFBooleanTrue) == .success,
                  bool(pathFields[0].0, "AXFocused") == true else { try fail("NATIVE_ACTION_FAILED", "Directory field focus was not observed") }
        }
        guard AXUIElementSetAttributeValue(pathFields[0].0, "AXValue" as CFString, directory as CFString) == .success,
              text(pathFields[0].0, "AXValue") == directory else { try fail("NATIVE_ACTION_FAILED", "Directory field value was not observed exactly") }
        phase = "directory_confirm"
        try perform(pathFields[0].0, action: "AXConfirm")
        var located: Observation?
        for _ in 0..<20 {
            let current = try observe()
            guard current.state["authentication_or_permission_prompt"] as? Bool != true,
                  NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier else { try fail("AUTHENTICATION_BOUNDARY", "A prompt or foreground application change interrupted directory confirmation") }
            if current.focusedWindow.map({ CFEqual($0, originalPanel) }) == true {
                located = current; break
            }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        if located == nil {
            let current = try observe()
            guard let focused = current.focusedWindow,
                  text(focused, "AXIdentifier") == "GoToWindow",
                  text(focused, "AXRole") == "AXSheet",
                  current.state["authentication_or_permission_prompt"] as? Bool != true,
                  NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier else {
                try fail("DIRECTORY_NOT_OBSERVED", "Directory confirmation did not return to the Save As panel; chooser was not submitted")
            }
            let focusedPath = current.nodes.first(where: { CFEqual($0.0, focused) })?.1["path"] as? String ?? ""
            guard !focusedPath.isEmpty else { try fail("DIRECTORY_NOT_OBSERVED", "The observed Go To sheet path is unavailable") }
            let closeButtons = current.nodes.filter {
                text($0.0, "AXIdentifier") == "CloseButton" && text($0.0, "AXRole") == "AXButton"
                    && text($0.0, "AXDescription") == "Close"
                    && ($0.1["path"] as? String ?? "").hasPrefix(focusedPath + "/")
            }
            guard closeButtons.count == 1 else { try fail("DIRECTORY_NOT_OBSERVED", "The observed Go To dismissal control is absent or ambiguous") }
            try perform(closeButtons[0].0)
            for _ in 0..<20 {
                let after = try observe()
                guard after.state["authentication_or_permission_prompt"] as? Bool != true,
                      NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier else { try fail("AUTHENTICATION_BOUNDARY", "A prompt or foreground application change interrupted directory confirmation") }
                if after.focusedWindow.map({ CFEqual($0, originalPanel) }) == true { located = after; break }
                try await Task.sleep(nanoseconds: 100_000_000)
            }
        }
        guard let located, CFEqual(try panel(located, kind: kind), originalPanel) else {
            try fail("DIRECTORY_NOT_OBSERVED", "Save As panel did not return after directory confirmation; chooser was not submitted")
        }
        var location = located
        if !location.nodes.contains(where: { $0.1["identifier"] as? String == "where popup" }) {
            try perform(try node(location, identifier: "NS_OPEN_SAVE_DISCLOSURE_TRIANGLE"))
            try await Task.sleep(nanoseconds: 120_000_000)
            location = try observe()
        }
        guard CFEqual(try panel(location, kind: kind), originalPanel),
              let whereNode = location.nodes.first(where: { $0.1["identifier"] as? String == "where popup" }),
              whereNode.1["value"] as? String == directoryURL.lastPathComponent,
              ((whereNode.1["url"] as? String).flatMap(URL.init(string:)).map({ $0.standardizedFileURL.path == directory }) ?? true)
              else { try fail("DIRECTORY_NOT_OBSERVED", "Requested directory transition was not observed; chooser was not submitted") }
        phase = "filename_entry"
        if kind == "save_as" || kind == "export_png_wav" { try setName(location, name: basename) }
        else {
            let rows = location.nodes.filter { text($0.0, "AXRole") == "AXTextField" && text($0.0, "AXValue") == basename && actions($0.0).contains("AXConfirm") }
            guard rows.count == 1 else { try fail("INPUT_SELECTION_UNAVAILABLE", "Requested input row is absent or ambiguous") }
            try perform(rows[0].0, action: "AXConfirm")
            return ["status": "native_chooser_submitted", "destination": destination.path, "completion": "unverified", "retry_safe": false]
        }
        try await Task.sleep(nanoseconds: 100_000_000)
        let ready = try observe()
        phase = "save_precondition"
        _ = try panel(ready, kind: kind); try ownedRoot(parameters, ready)
        guard ready.application?.processIdentifier == app.processIdentifier,
              NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier,
              ready.state["authentication_or_permission_prompt"] as? Bool != true,
              !FileManager.default.fileExists(atPath: destination.path) else { try fail("PRECONDITION_CHANGED", "Process, prompt or destination changed before save") }
        guard let name = ready.nodes.first(where: { $0.1["identifier"] as? String == "saveAsNameTextField" })?.1["value"] as? String,
              name == basename else { try fail("DESTINATION_MISMATCH", "Native save filename changed") }
        phase = "save_dispatch"
        try perform(try node(ready, identifier: "OKButton", role: "AXButton"))
        return ["status": "native_chooser_submitted", "destination": destination.path, "completion": "unverified", "retry_safe": false]
    }
    func continueChooser(_ initial: Observation, parameters: [String: Any]) async throws -> [String: Any] {
        phase = "chooser_continue_preflight"
        guard let directory = parameters["directory"] as? String,
              let basename = parameters["basename"] as? String,
              let kind = parameters["kind"] as? String,
              ["import", "export_png_wav"].contains(kind),
              let app = initial.application,
              typedChooserIsObserved(initial, kind: kind),
              hasOnlyExpectedChooserModal(initial, kind: kind),
              initial.state["authentication_or_permission_prompt"] as? Bool != true else {
            try fail("CHOOSER_MISMATCH", "Continue requires the exact existing typed chooser without an unrelated modal or prompt")
        }
        let originalPID = app.processIdentifier
        let originalLaunch = initial.state["launch_time"] as? Double
        try ownedRoot(parameters, initial)
        let targetDirectory = URL(fileURLWithPath: directory).standardizedFileURL
        let target = targetDirectory.appendingPathComponent(basename)
        guard targetDirectory.path == directory,
              targetDirectory.resolvingSymlinksInPath() == targetDirectory,
              target.resolvingSymlinksInPath() == target,
              FileManager.default.fileExists(atPath: target.path) == (kind == "import") else {
            try fail("INVALID_DESTINATION", "The requested chooser file must match its existing-input or new-output state")
        }
        if kind == "export_png_wav" {
            try verifyExportDirectory(parameters, directory: targetDirectory, basename: basename)
        }

        func verify(_ current: Observation) throws {
            try ownedRoot(parameters, current)
            guard current.application?.processIdentifier == originalPID,
                  (current.state["launch_time"] as? Double) == originalLaunch,
                  current.state["authentication_or_permission_prompt"] as? Bool != true,
                  NSWorkspace.shared.frontmostApplication?.processIdentifier == originalPID,
                  hasOnlyExpectedChooserModal(current, kind: kind) else {
                try fail("CHOOSER_MISMATCH", "The same owned process and typed chooser must remain active without a prompt")
            }
        }
        func isDirectoryReady(_ current: Observation) -> Bool {
            guard typedChooserIsFocused(current, kind: kind),
                  let whereNode = current.nodes.first(where: { $0.1["identifier"] as? String == "where popup" }),
                  whereNode.1["value"] as? String == targetDirectory.lastPathComponent,
                  (whereNode.1["url"] as? String).flatMap(URL.init(string:)).map({ $0.standardizedFileURL.path == directory }) ?? false else { return false }
            if kind == "export_png_wav" {
                return true
            }
            return current.nodes.contains {
                text($0.0, "AXValue") == basename && text($0.0, "AXRole") == "AXTextField"
                    && (attribute($0.0, "AXURL") as? URL)?.standardizedFileURL == target
                    && bool($0.0, "AXSelected") == true
            }
        }
        func isParentedGoToReady(_ current: Observation) -> Bool {
            typedChooserIsFocused(current, kind: kind)
                && current.focusedWindow.map {
                    text($0, "AXIdentifier") == "GoToWindow" && text($0, "AXRole") == "AXSheet"
                } == true
        }
        func settle(_ stage: String, ready: (Observation) -> Bool) async throws -> Observation {
            let deadline = ProcessInfo.processInfo.systemUptime + 5
            var previous: String?
            var observations = 0
            var lastReady = false
            var fingerprintStable = false
            while ProcessInfo.processInfo.systemUptime < deadline {
                try Task.checkCancellation()
                let current = try observe()
                try verify(current)
                observations += 1
                phase = "chooser_\(stage)_readiness"
                let fingerprint = current.state["fingerprint"] as? String
                lastReady = ready(current)
                fingerprintStable = fingerprint != nil && fingerprint == previous
                continuationDiagnostics = ["stage": stage, "last_ready": lastReady,
                                           "fingerprint_stable": fingerprintStable,
                                           "observations": observations]
                if lastReady && fingerprintStable { return current }
                previous = fingerprint
                let remaining = deadline - ProcessInfo.processInfo.systemUptime
                if remaining > 0 { try await Task.sleep(nanoseconds: UInt64(min(0.1, remaining) * 1_000_000_000)) }
            }
            try fail("CHOOSER_UNAVAILABLE", "The exact typed chooser did not reach the requested observed state")
        }

        var current = initial
        if !isDirectoryReady(current) {
            if current.focusedWindow.map({ text($0, "AXIdentifier") }) != "GoToWindow" {
                let opened = try await chooserDestination(current, parameters: parameters)
                guard opened["status"] as? String == "native_chooser_directory_requested" else {
                    try fail("CHOOSER_MISMATCH", "Chooser directory navigation did not reach its recorded stage")
                }
                current = try await settle("go_to_sheet") { isParentedGoToReady($0) }
            }
            if !isDirectoryReady(current) {
                guard current.focusedWindow.map({ text($0, "AXIdentifier") }) == "GoToWindow" else {
                    try fail("CHOOSER_MISMATCH", "The exact typed chooser or its parented Go To sheet is required")
                }
                _ = try await continueDirectoryEntry(current, parameters: parameters)
                current = try await settle("target_directory") { isDirectoryReady($0) }
            }
        }
        guard isDirectoryReady(current) else {
            try fail("INPUT_NOT_OBSERVED", "The requested file or export filename is not observed in the exact folder")
        }
        let result = try await continueDirectoryEntry(current, parameters: parameters)
        guard result["status"] as? String == "native_chooser_submitted" else {
            try fail("CHOOSER_NOT_SUBMITTED", "The exact observed chooser destination was not submitted")
        }
        return result
    }

    func verifyExportDirectory(_ parameters: [String: Any], directory: URL, basename: String) throws {
        guard let identity = parameters["output_directory_identity"] as? [String: Any],
              let device = (identity["device"] as? NSNumber)?.int64Value,
              let inode = (identity["inode"] as? NSNumber)?.int64Value,
              let attributes = try? FileManager.default.attributesOfItem(atPath: directory.path),
              attributes[.type] as? FileAttributeType == .typeDirectory,
              attributes[.systemNumber] as? NSNumber == NSNumber(value: device),
              attributes[.systemFileNumber] as? NSNumber == NSNumber(value: inode),
              !FileManager.default.fileExists(atPath: directory.appendingPathComponent(basename).path),
              !FileManager.default.fileExists(atPath: directory.appendingPathComponent(basename + ".xml").path) else {
            try fail("DESTINATION_CHANGED", "The journaled export directory or new output targets changed")
        }
    }
    func verifyTopmostChooser(_ observation: Observation, window: AXUIElement) throws {
        guard let app = observation.application,
              let focused = observation.focusedWindow, CFEqual(focused, window),
              let f = frame(window),
              observation.state["authentication_or_permission_prompt"] as? Bool != true,
              NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier else {
            try fail("FOCUS_CHANGED", "The exact foreground chooser is required immediately before submission")
        }
        let expectedFrame = CGRect(x: f[0], y: f[1], width: f[2], height: f[3])
        let serverWindows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements],
                                                        kCGNullWindowID) as? [[String: Any]] ?? []
        guard let top = serverWindows.first(where: { item in
                  if item[kCGWindowOwnerName as String] as? String == "Window Server",
                     item[kCGWindowLayer as String] as? Int == Int(CGWindowLevelForKey(.cursorWindow)) { return false }
                  guard (item[kCGWindowAlpha as String] as? Double ?? 1) > 0,
                        let bounds = item[kCGWindowBounds as String] as? [String: Any],
                        let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary) else { return false }
                  return rect.intersects(expectedFrame)
              }), top[kCGWindowOwnerPID as String] as? Int == Int(app.processIdentifier),
              let bounds = top[kCGWindowBounds as String] as? [String: Any],
              let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary),
              abs(rect.minX - f[0]) <= 2, abs(rect.minY - f[1]) <= 2,
              abs(rect.width - f[2]) <= 2, abs(rect.height - f[3]) <= 2 else {
            try fail("FOCUS_CHANGED", "The exact observed chooser is not the topmost Character Animator window")
        }
    }
    func continueDirectoryEntry(_ observation: Observation, parameters: [String: Any]) async throws -> [String: Any] {
        var observation = observation
        phase = "directory_resume_preflight"
        guard let kind = parameters["kind"] as? String, ["save_as", "import", "export_png_wav"].contains(kind),
              let directory = parameters["directory"] as? String,
              let basename = parameters["basename"] as? String,
              directory.hasPrefix("/"),
              let app = observation.application,
              observation.state["authentication_or_permission_prompt"] as? Bool != true else {
            try fail("CHOOSER_MISMATCH", "Expected the current task-owned native chooser")
        }
        let directoryURL = URL(fileURLWithPath: directory).standardizedFileURL
        let selectedURL = directoryURL.appendingPathComponent(basename)
        let entryPath = kind == "import" ? selectedURL.path : directory
        guard directoryURL.path == directory, directoryURL.resolvingSymlinksInPath() == directoryURL,
              selectedURL.resolvingSymlinksInPath() == selectedURL,
              FileManager.default.fileExists(atPath: selectedURL.path) == (kind == "import") else {
            try fail("INVALID_DESTINATION", "Native chooser path does not match the requested existing input or new project")
        }
        try ownedRoot(parameters, observation)
        if let sheetElement = observation.focusedWindow,
           text(sheetElement, "AXIdentifier") == "GoToWindow",
           text(sheetElement, "AXRole") == "AXSheet",
           let sheet = observation.nodes.first(where: { CFEqual($0.0, sheetElement) }),
           let sheetPath = sheet.1["path"] as? String,
           let sheetFrame = frame(sheetElement) {
            let matchingSheets = observation.nodes.filter {
                text($0.0, "AXIdentifier") == "GoToWindow" && text($0.0, "AXRole") == "AXSheet"
                    && CFEqual($0.0, sheetElement)
            }
            var uniqueSheets: [AXUIElement] = []
            for candidate in matchingSheets.map(\.0) where !uniqueSheets.contains(where: { CFEqual($0, candidate) }) {
                uniqueSheets.append(candidate)
            }
            guard uniqueSheets.count == 1, CFEqual(uniqueSheets[0], sheetElement) else {
                try fail("CHOOSER_MISMATCH", "The exact focused Go To sheet is absent or ambiguous")
            }
            let fields = observation.nodes.filter {
                text($0.0, "AXIdentifier") == "PathTextField" && text($0.0, "AXRole") == "AXTextField"
                    && ($0.1["path"] as? String ?? "").hasPrefix(sheetPath + "/")
                    && actions($0.0).contains("AXConfirm")
            }
            guard fields.count == 1 else {
                try fail("CHOOSER_MISMATCH", "The focused Go To sheet path field is absent or ambiguous")
            }
            phase = "directory_focus"
            try await focusApplication(observation)
            try perform(sheetElement, action: "AXRaise")
            var fieldFocusSettable: DarwinBoolean = false
            if AXUIElementIsAttributeSettable(fields[0].0, "AXFocused" as CFString, &fieldFocusSettable) == .success,
               fieldFocusSettable.boolValue {
                guard AXUIElementSetAttributeValue(fields[0].0, "AXFocused" as CFString, kCFBooleanTrue) == .success else {
                    try fail("FOCUS_UNAVAILABLE", "The retained Go To field could not regain focus")
                }
            }
            var focusedSheet: Observation?
            var previousFingerprint: String?
            var stableCount = 0
            for _ in 0..<15 {
                try await Task.sleep(nanoseconds: 100_000_000)
                let current = try observe()
                guard current.state["authentication_or_permission_prompt"] as? Bool != true,
                      NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier else {
                    try fail("AUTHENTICATION_BOUNDARY", "A prompt or foreground change interrupted Go To focus")
                }
                if let focused = current.focusedWindow,
                   text(focused, "AXIdentifier") == "GoToWindow", text(focused, "AXRole") == "AXSheet",
                   let f = frame(focused), zip(f, sheetFrame).allSatisfy({ abs($0.0 - $0.1) <= 2 }),
                   let focusedNode = current.nodes.first(where: { CFEqual($0.0, focused) }),
                   let focusedPath = focusedNode.1["path"] as? String,
                   current.nodes.contains(where: { text($0.0, "AXIdentifier") == "PathTextField"
                       && text($0.0, "AXRole") == "AXTextField"
                       && ($0.1["path"] as? String ?? "").hasPrefix(focusedPath + "/")
                       && actions($0.0).contains("AXConfirm") }) {
                    let fingerprint = current.state["fingerprint"] as? String
                    stableCount = fingerprint != nil && fingerprint == previousFingerprint ? stableCount + 1 : 0
                    previousFingerprint = fingerprint
                    focusedSheet = current
                    if stableCount >= 1 { break }
                } else {
                    focusedSheet = nil
                    previousFingerprint = nil
                    stableCount = 0
                }
            }
            guard let focusedSheet else { try fail("CHOOSER_MISMATCH", "The exact Go To sheet did not regain focus after native activation") }
            observation = focusedSheet
            try ownedRoot(parameters, observation)
            guard let activeSheet = observation.focusedWindow,
                  let activeSheetNode = observation.nodes.first(where: { CFEqual($0.0, activeSheet) }),
                  let activePath = activeSheetNode.1["path"] as? String,
                  let activeFrame = frame(activeSheet) else {
                try fail("CHOOSER_MISMATCH", "The focused Go To sheet identity was lost before entry")
            }
            let activeFields = observation.nodes.filter {
                text($0.0, "AXIdentifier") == "PathTextField" && text($0.0, "AXRole") == "AXTextField"
                    && ($0.1["path"] as? String ?? "").hasPrefix(activePath + "/")
                    && actions($0.0).contains("AXConfirm")
            }
            guard activeFields.count == 1, let activeField = activeFields.first?.0 else {
                try fail("CHOOSER_MISMATCH", "The exact focused Go To path field is absent or ambiguous")
            }
            let serverWindows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements],
                                                            kCGNullWindowID) as? [[String: Any]] ?? []
            let center = CGPoint(x: activeFrame[0] + activeFrame[2] / 2,
                                 y: activeFrame[1] + activeFrame[3] / 2)
            let topmostAtSheet = serverWindows.first { item in
                if item[kCGWindowOwnerName as String] as? String == "Window Server",
                   item[kCGWindowLayer as String] as? Int == Int(CGWindowLevelForKey(.cursorWindow)) { return false }
                guard (item[kCGWindowAlpha as String] as? Double ?? 1) > 0,
                      let bounds = item[kCGWindowBounds as String] as? [String: Any],
                      let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary) else { return false }
                return rect.contains(center)
            }
            guard topmostAtSheet?[kCGWindowOwnerPID as String] as? Int == Int(app.processIdentifier) else {
                try fail("FOCUS_CHANGED", "The focused Go To sheet is not the topmost visible Character Animator window")
            }
            dispatched = true
            guard AXUIElementSetAttributeValue(activeField, "AXFocused" as CFString, kCFBooleanTrue) == .success,
                  AXUIElementSetAttributeValue(activeField, "AXValue" as CFString, entryPath as CFString) == .success,
                  text(activeField, "AXValue") == entryPath else {
                try fail("FOCUS_UNAVAILABLE", "The exact owned Save As destination path was not entered and read back")
            }
            observation = try observe()
            guard let readbackSheet = observation.focusedWindow,
                  text(readbackSheet, "AXIdentifier") == "GoToWindow",
                  let readbackNode = observation.nodes.first(where: { CFEqual($0.0, readbackSheet) }),
                  let readbackPath = readbackNode.1["path"] as? String,
                  let readbackFrame = frame(readbackSheet),
                  zip(readbackFrame, activeFrame).allSatisfy({ abs($0.0 - $0.1) <= 2 }),
                  observation.nodes.contains(where: { text($0.0, "AXIdentifier") == "PathTextField"
                      && ($0.1["path"] as? String ?? "").hasPrefix(readbackPath + "/")
                      && text($0.0, "AXValue") == entryPath }) else {
                try fail("CHOOSER_MISMATCH", "The exact requested directory was not read back from the focused Go To sheet")
            }
            try ownedRoot(parameters, observation)
        }
        func submitSave(_ panelState: Observation) async throws -> [String: Any] {
            _ = try panel(panelState, kind: kind)
            try await focusApplication(panelState)
            guard NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier,
                  panelState.state["authentication_or_permission_prompt"] as? Bool != true else {
                try fail("FOCUS_CHANGED", "Character Animator must remain foreground before directory verification")
            }
            var expanded = panelState
            if !expanded.nodes.contains(where: { $0.1["identifier"] as? String == "where popup" }) {
                try perform(try node(expanded, identifier: "NS_OPEN_SAVE_DISCLOSURE_TRIANGLE"))
                try await Task.sleep(nanoseconds: 120_000_000)
                expanded = try observe()
            }
            _ = try panel(expanded, kind: kind)
            let whereNodes = expanded.nodes.filter { $0.1["identifier"] as? String == "where popup" }
            guard whereNodes.count == 1,
                  whereNodes[0].1["value"] as? String == directoryURL.lastPathComponent,
                  ((whereNodes[0].1["url"] as? String).flatMap(URL.init(string:)).map({ $0.standardizedFileURL.path == directory }) ?? true) else {
                try fail("DIRECTORY_NOT_OBSERVED", "Save As Where control does not identify the requested directory")
            }
            if kind == "import" {
                let candidates = expanded.nodes.filter {
                    text($0.0, "AXValue") == basename && text($0.0, "AXRole") == "AXTextField"
                        && (actions($0.0).contains("AXOpen") || actions($0.0).contains("AXConfirm"))
                }
                var files: [AXUIElement] = []
                for candidate in candidates.map(\.0) where !files.contains(where: { CFEqual($0, candidate) }) {
                    files.append(candidate)
                }
                guard files.count == 1 else { try fail("INPUT_NOT_OBSERVED", "The exact retained import filename is absent or ambiguous in the native chooser") }
                guard (attribute(files[0], "AXURL") as? URL)?.standardizedFileURL == selectedURL else {
                    try fail("INPUT_NOT_OBSERVED", "The native import filename URL does not match the retained file")
                }
                guard bool(files[0], "AXSelected") == true else {
                    try fail("INPUT_NOT_OBSERVED", "Go To must select the exact retained file before Import")
                }
                let selected = try observe()
                try ownedRoot(parameters, selected)
                guard selected.state["authentication_or_permission_prompt"] as? Bool != true else {
                    try fail("AUTHENTICATION_BOUNDARY", "A prompt interrupted native import selection")
                }
                _ = try panel(selected, kind: "import")
                let button = try node(selected, identifier: "OKButton", role: "AXButton")
                guard text(button, "AXTitle") == "Import", bool(button, "AXEnabled") == true else {
                    try fail("INPUT_NOT_OBSERVED", "Native import selection did not enable its observed Import control")
                }
                guard let expectedHash = parameters["expected_sha256"] as? String,
                      expectedHash.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
                      let attributesBefore = try? FileManager.default.attributesOfItem(atPath: selectedURL.path),
                      attributesBefore[.type] as? FileAttributeType == .typeRegular,
                      (try? selectedURL.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) != true,
                      let expectedSize = (attributesBefore[.size] as? NSNumber)?.intValue,
                      let bytes = try? Data(contentsOf: selectedURL, options: [.mappedIfSafe]),
                      let attributesAfter = try? FileManager.default.attributesOfItem(atPath: selectedURL.path),
                      attributesBefore[.systemNumber] as? NSNumber == attributesAfter[.systemNumber] as? NSNumber,
                      attributesBefore[.systemFileNumber] as? NSNumber == attributesAfter[.systemFileNumber] as? NSNumber,
                      attributesBefore[.size] as? NSNumber == attributesAfter[.size] as? NSNumber,
                      attributesBefore[.modificationDate] as? Date == attributesAfter[.modificationDate] as? Date,
                      bytes.count == expectedSize,
                      sha(bytes) == expectedHash else {
                    try fail("INPUT_CHANGED", "The selected import copy no longer matches its retained SHA-256")
                }
                guard selected.application?.processIdentifier == app.processIdentifier,
                      selected.state["authentication_or_permission_prompt"] as? Bool != true,
                      NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier,
                      selected.state["project_roots"] as? [String] == [parameters["project_root"] as? String ?? ""] else {
                    try fail("PRECONDITION_CHANGED", "The same foreground process and owned project are required immediately before Import")
                }
                let submitReady = try observe()
                guard submitReady.state["fingerprint"] as? String == selected.state["fingerprint"] as? String,
                      submitReady.application?.processIdentifier == app.processIdentifier,
                      submitReady.state["authentication_or_permission_prompt"] as? Bool != true,
                      NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier,
                      submitReady.state["project_roots"] as? [String] == [parameters["project_root"] as? String ?? ""],
                      (try? panel(submitReady, kind: "import")) != nil else {
                    try fail("PRECONDITION_CHANGED", "The exact selected Import panel changed during source verification")
                }
                let readyButton = try node(submitReady, identifier: "OKButton", role: "AXButton")
                guard text(readyButton, "AXTitle") == "Import", bool(readyButton, "AXEnabled") == true else {
                    try fail("PRECONDITION_CHANGED", "The observed Import control changed during source verification")
                }
                let stillSelected = submitReady.nodes.contains {
                    text($0.0, "AXValue") == basename && text($0.0, "AXRole") == "AXTextField"
                        && (attribute($0.0, "AXURL") as? URL)?.standardizedFileURL == selectedURL
                        && bool($0.0, "AXSelected") == true
                }
                guard stillSelected else { try fail("INPUT_NOT_OBSERVED", "The retained import file selection changed before submission") }
                try verifyTopmostChooser(submitReady, window: panel(submitReady, kind: "import"))
                phase = "import_dispatch"
                submissionMayHaveStarted = true
                try perform(readyButton)
                return ["status": "native_chooser_submitted", "destination": selectedURL.path,
                        "completion": "unverified", "retry_safe": false]
            }
            phase = "filename_entry"
            try setName(expanded, name: basename)
            try await Task.sleep(nanoseconds: 100_000_000)
            let ready = try observe()
            try ownedRoot(parameters, ready)
            guard (try? panel(ready, kind: kind)) != nil,
                  ready.state["authentication_or_permission_prompt"] as? Bool != true,
                  ready.application?.processIdentifier == app.processIdentifier,
                  NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier,
                  !FileManager.default.fileExists(atPath: directoryURL.appendingPathComponent(basename).path),
                  ready.nodes.first(where: { $0.1["identifier"] as? String == "where popup" })?.1["value"] as? String == directoryURL.lastPathComponent,
                  ready.nodes.first(where: { $0.1["identifier"] as? String == "saveAsNameTextField" })?.1["value"] as? String == basename else {
                try fail("PRECONDITION_CHANGED", "Directory, filename or owned Save As panel changed before submit")
            }
            let saveButton = try node(ready, identifier: "OKButton", role: "AXButton")
            guard text(saveButton, "AXTitle") == "Save", bool(saveButton, "AXEnabled") == true else {
                try fail("PRECONDITION_CHANGED", "Observed Save button is disabled or changed")
            }
            if kind == "export_png_wav" {
                try verifyExportDirectory(parameters, directory: directoryURL, basename: basename)
            }
            try verifyTopmostChooser(ready, window: try panel(ready, kind: kind))
            phase = "save_dispatch"
            submissionMayHaveStarted = true
            try perform(saveButton)
            return ["status": "native_chooser_submitted", "destination": directoryURL.appendingPathComponent(basename).path,
                    "directory": directory, "filename": basename, "completion": "unverified", "retry_safe": false]
        }
        let chooserIdentifier = kind == "import" ? "open-panel" : "save-panel"
        if observation.focusedWindow.map({ text($0, "AXIdentifier") }) == chooserIdentifier {
            return try await submitSave(observation)
        }
        guard let focused = observation.focusedWindow,
              text(focused, "AXIdentifier") == "GoToWindow", text(focused, "AXRole") == "AXSheet",
              let root = observation.root,
              let field = axElement(attribute(root, "AXFocusedUIElement")),
              text(field, "AXIdentifier") == "PathTextField", text(field, "AXValue") == entryPath else {
            try fail("CHOOSER_MISMATCH", "The exact requested Go To directory field must be focused")
        }
        phase = "directory_confirm"
        try chooserKey(observation, key: 36)
        return ["status": "native_directory_requested", "directory": directory,
                "filename": basename, "completion": "unverified", "retry_safe": false]
    }
    func projectSelect(_ parameters: [String: Any], observation: Observation) async throws -> [String: Any] {
        phase = "project_select_preflight"
        guard let name = parameters["name"] as? String, !name.isEmpty, name.count <= 128,
              let itemType = parameters["item_type"] as? String,
              ["puppet", "audio", "scene"].contains(itemType),
              let extend = parameters["extend"] as? Bool,
              let app = observation.application else {
            try fail("INVALID_ARGUMENT", "name, puppet/audio/scene item_type and boolean extend are required")
        }
        try ownedRoot(parameters, observation)
        guard observation.state["authentication_or_permission_prompt"] as? Bool != true,
              observation.focusedWindow.map({ bool($0, "AXModal") == true }) != true,
              CGPreflightScreenCaptureAccess(), CGPreflightPostEventAccess() else {
            try fail("APPLICATION_UNAVAILABLE", "Visible nonmodal Character Animator, screenshot and existing event access are required")
        }
        let originalRoots = observation.state["project_roots"] as? [String] ?? []
        try await focusApplication(observation)
        try await Task.sleep(nanoseconds: 150_000_000)
        let current = try observe()
        try ownedRoot(parameters, current)
        guard current.application?.processIdentifier == app.processIdentifier,
              current.state["authentication_or_permission_prompt"] as? Bool != true,
              current.state["project_roots"] as? [String] == originalRoots,
              current.focusedWindow.map({ bool($0, "AXModal") == true }) != true else {
            try fail("STALE_STATE", "Character Animator project, process or modal state changed before project selection")
        }
        let captureResult = try await capture(current, parameters: [:])
        guard let ocr = captureResult["ocr"] as? [[String: Any]],
              let windowID = captureResult["window_id"] as? UInt32,
              let frame = captureResult["frame"] as? [Double], frame.count == 4 else {
            try fail("CAPTURE_FAILED", "Project panel OCR or exact native window evidence is unavailable")
        }
        func trimLeadingIcons(_ value: String) -> String {
            String(value.unicodeScalars.drop(while: { !CharacterSet.alphanumerics.contains($0) }))
        }
        func bounds(_ item: [String: Any]) -> [Double]? {
            guard let value = item["bounds"] as? [Double], value.count == 4 else { return nil }
            return value
        }
        let normalizedType = itemType.prefix(1).uppercased() + itemType.dropFirst().lowercased()
        let names = ocr.filter { token in
            guard let raw = token["text"] as? String, let box = bounds(token),
                  (token["confidence"] as? Double ?? 0) >= 0.8 else { return false }
            let centerX = box[0] + box[2] / 2, centerY = box[1] + box[3] / 2
            return centerX < 0.18 && centerY > 0.70
                && trimLeadingIcons(raw).caseInsensitiveCompare(name) == .orderedSame
        }
        let types = ocr.filter { token in
            guard let raw = token["text"] as? String, let box = bounds(token),
                  (token["confidence"] as? Double ?? 0) >= 0.8 else { return false }
            let centerX = box[0] + box[2] / 2
            return centerX >= 0.08 && centerX <= 0.35
                && raw.trimmingCharacters(in: .whitespacesAndNewlines).caseInsensitiveCompare(normalizedType) == .orderedSame
        }
        var matches: [(name: [String: Any], type: [String: Any], nameBounds: [Double], typeBounds: [Double])] = []
        for nameToken in names {
            guard let nameBounds = bounds(nameToken) else { continue }
            let nameY = nameBounds[1] + nameBounds[3] / 2
            for typeToken in types {
                guard let typeBounds = bounds(typeToken) else { continue }
                let typeY = typeBounds[1] + typeBounds[3] / 2
                if abs(nameY - typeY) <= min(0.006, max(nameBounds[3], typeBounds[3]) / 2) {
                    matches.append((nameToken, typeToken, nameBounds, typeBounds))
                }
            }
        }
        guard matches.count == 1, let match = matches.first else {
            try fail("PROJECT_ITEM_AMBIGUOUS", "Expected one visible Project-panel name and matching Puppet/Audio/Scene type row")
        }
        let nameBox = match.nameBounds
        let point = CGPoint(x: frame[0] + (nameBox[0] + nameBox[2] / 2) * frame[2],
                            y: frame[1] + (1 - (nameBox[1] + nameBox[3] / 2)) * frame[3])
        guard point.x >= frame[0], point.x <= frame[0] + frame[2],
              point.y >= frame[1], point.y <= frame[1] + frame[3] else {
            try fail("PROJECT_ITEM_AMBIGUOUS", "OCR-derived project selection point is outside the captured window")
        }
        let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements],
                                                  kCGNullWindowID) as? [[String: Any]] ?? []
        let topmost = windows.first { item in
            if item[kCGWindowOwnerName as String] as? String == "Window Server",
               item[kCGWindowLayer as String] as? Int == Int(CGWindowLevelForKey(.cursorWindow)) { return false }
            guard (item[kCGWindowAlpha as String] as? Double ?? 1) > 0,
                  let rectData = item[kCGWindowBounds as String] as? [String: Any],
                  let rect = CGRect(dictionaryRepresentation: rectData as CFDictionary) else { return false }
            return rect.contains(point)
        }
        guard (topmost?[kCGWindowNumber as String] as? NSNumber)?.uint32Value == windowID,
              topmost?[kCGWindowOwnerPID as String] as? Int == Int(app.processIdentifier),
              NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier,
              CGPreflightPostEventAccess(), current.state["authentication_or_permission_prompt"] as? Bool != true,
              let down = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown,
                                 mouseCursorPosition: point, mouseButton: .left),
              let up = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp,
                               mouseCursorPosition: point, mouseButton: .left) else {
            try fail("FOCUS_CHANGED", "OCR-selected Project row is not under the exact visible Character Animator window")
        }
        if extend { down.flags = [.maskCommand]; up.flags = [.maskCommand] }
        phase = "project_select_dispatch"
        dispatched = true
        primaryCommandDispatched = true
        down.post(tap: .cghidEventTap)
        up.post(tap: .cghidEventTap)
        try await Task.sleep(nanoseconds: 120_000_000)
        let after = try observe()
        try ownedRoot(parameters, after)
        guard after.state["authentication_or_permission_prompt"] as? Bool != true,
              after.state["project_roots"] as? [String] == originalRoots else {
            try fail("STALE_STATE", "Project or prompt state changed after native Project-row selection")
        }
        return ["state": after.state, "result": ["status": "native_project_item_selected",
            "completion": "unverified", "selection_evidence": ["ocr_identity": "observed_ocr_name_and_type",
                "name": name, "item_type": itemType, "name_text": match.name["text"] ?? "",
                "type_text": match.type["text"] ?? "", "window_id": windowID,
                "name_bounds": match.nameBounds, "type_bounds": match.typeBounds,
                "selection_point": [point.x, point.y], "extend": extend,
                "capture_sha256": captureResult["sha256"] ?? ""]]]
    }
    func run(_ request: [String: Any]) async throws -> [String: Any] {
        guard let operation = request["operation"] as? String else { try fail("INVALID_ARGUMENT", "operation is required") }
        let parameters = request["parameters"] as? [String: Any] ?? [:]
        phase = "observation"
        let observation = try observe(full: operation != "status")
        if operation == "status" || operation == "inspect" { return ["state": observation.state, "result": ["status": "observed"]] }
        if operation == "open_prepared_copy" { return try await openPreparedCopy(parameters, observation) }
        if operation == "capture" {
            guard observation.state["authentication_or_permission_prompt"] as? Bool != true else { try fail("AUTHENTICATION_BOUNDARY", "Application prompt requires manual handling") }
            return ["state": observation.state, "result": try await capture(observation, parameters: parameters)]
        }
        phase = "precondition"
        if operation == "focus" {
            try expectedFocusIdentity(request, observation: observation)
        } else {
            try expected(request, observation: observation)
        }
        if operation == "chooser_continue_directory" {
            let result = try await continueDirectoryEntry(observation, parameters: parameters)
            return ["state": try observe().state, "result": result]
        }
        if operation == "continue_chooser" {
            let result = try await continueChooser(observation, parameters: parameters)
            return ["state": try observe().state, "result": result]
        }
        if operation == "focus" {
            try ownedRoot(parameters, observation)
            guard let kind = parameters["kind"] as? String else { try fail("INVALID_ARGUMENT", "Focus requires the expected native chooser kind") }
            guard ["save_as", "import", "export_png_wav", "open_project"].contains(kind) else {
                try fail("INVALID_ARGUMENT", "Focus requires an exact supported chooser kind")
            }
            guard typedChooserIsObserved(observation, kind: kind),
                  (safeBackgroundMain(observation) || hasOnlyExpectedChooserModal(observation, kind: kind)) else {
                try fail("CHOOSER_MISMATCH", "Activation requires the exact same-process native chooser and no unrelated modal")
            }
            try await focusApplication(observation)
            let after = try observe()
            try ownedRoot(parameters, after)
            guard (after.state["pid"] as? Int) == (observation.state["pid"] as? Int),
                  (after.state["launch_time"] as? Double) == (observation.state["launch_time"] as? Double),
                  after.state["bundle_id"] as? String == characterBundleID,
                  after.state["application"] as? String == applicationURL.path,
                  after.state["authentication_or_permission_prompt"] as? Bool != true,
                  after.state["frontmost"] as? Bool == true,
                  hasOnlyExpectedChooserModal(after, kind: kind),
                  typedChooserIsFocused(after, kind: kind) else {
                try fail("CHOOSER_MISMATCH", "After activation, the same process must expose the requested chooser without a prompt")
            }
            return ["state": after.state, "result": ["status": "native_application_focused", "completion": "observed", "ui_only": true]]
        } else if operation == "project_select" {
            return try await projectSelect(parameters, observation: observation)
        } else if commandPaths[operation] != nil {
            try ownedRoot(parameters, observation)
            guard observation.focusedWindow.map({ bool($0, "AXModal") == true }) != true else { try fail("UNEXPECTED_MODAL", "Resolve the current modal before requesting another command") }
            try await focusApplication(observation)
            let ready = try observe()
            try ownedRoot(parameters, ready)
            guard ready.state["authentication_or_permission_prompt"] as? Bool != true,
                  ready.focusedWindow.map({ bool($0, "AXModal") == true }) != true else {
                try fail("UNEXPECTED_MODAL", "A prompt or modal interrupted native menu activation")
            }
            // Activation intentionally changes frontmost and hidden. Every other observed
            // precondition must remain identical, and the target must now be active.
            var activatedState = observation.state
            activatedState.removeValue(forKey: "fingerprint")
            activatedState["frontmost"] = true
            activatedState["hidden"] = false
            if observation.focusedWindow == nil && safeBackgroundMain(observation) {
                let canvases = observation.nodes.filter { element, _ in
                    text(element, "AXRole") == "AXLayoutArea"
                        && text(element, "AXTitle") != nil && frame(element) != nil
                        && bool(element, "AXModal") != true
                }
                if canvases.count == 1, let focused = ready.focusedWindow,
                   CFEqual(focused, canvases[0].0) {
                    activatedState["focused_window"] = ready.state["focused_window"]
                }
            }
            guard ready.state["frontmost"] as? Bool == true,
                  ready.state["fingerprint"] as? String == sha(try canonical(fingerprintValue(activatedState))) else {
                try fail("STALE_STATE", "Native state changed during activation; inspect the current selection before dispatch")
            }
            guard let command = ready.menuElements[operation] else { try fail("CAPABILITY_UNAVAILABLE", "Native command is not exposed in this application version") }
            phase = "menu_dispatch"
            try perform(command)
        } else if operation == "chooser_destination" {
            let result = try await chooserDestination(observation, parameters: parameters)
            return ["state": try observe().state, "result": result]
        } else if operation == "chooser_expand" {
            _ = try panel(observation)
            let control = try node(observation, identifier: "NS_OPEN_SAVE_DISCLOSURE_TRIANGLE")
            try perform(control)
        } else if operation == "chooser_name" {
            _ = try panel(observation)
            guard let name = parameters["name"] as? String else { try fail("INVALID_ARGUMENT", "name is required") }
            try setName(observation, name: name)
        } else if operation == "chooser_folder" {
            _ = try panel(observation)
            guard let name = parameters["name"] as? String, !name.isEmpty, !name.contains("/"),
                  !["icloud", "google drive", "dropbox", "network"].contains(where: name.lowercased().contains) else { try fail("INVALID_ARGUMENT", "Choose an explicit local folder name") }
            let candidates = observation.nodes.filter { item in
                text(item.0, "AXValue") == name && text(item.0, "AXRole") == "AXTextField" && actions(item.0).contains("AXOpen")
            }
            if candidates.count == 1 { try perform(candidates[0].0, action: "AXOpen") }
            else {
                let popup = try node(observation, identifier: "where popup")
                // A second request selects an already observed ancestor menu item; no arbitrary menu path is accepted.
                let menuCandidates = observation.nodes.filter { text($0.0, "AXTitle") == name && text($0.0, "AXRole") == "AXMenuItem" && text($0.0, "AXIdentifier") == "retargetFromMenuItem:" }
                guard menuCandidates.count == 1 else {
                    if children(popup).isEmpty { try perform(popup); return ["state": try observe().state, "result": ["status": "folder_menu_opened", "completion": "unverified"]] }
                    try fail("AMBIGUOUS_FOLDER", "Folder name is absent or ambiguous in the observed native chooser")
                }
                try perform(menuCandidates[0].0)
            }
        } else if operation == "chooser_submit" {
            try fail("UNSUPPORTED_OPERATION", "Save As submission is part of the bounded project-copy continuation")
        } else if operation == "chooser_cancel" {
            guard let kind = parameters["kind"] as? String else { try fail("INVALID_ARGUMENT", "Chooser kind is required") }
            if kind == "go_to_folder" {
                guard let focused = observation.focusedWindow,
                      text(focused, "AXRole") == "AXSheet",
                      text(focused, "AXIdentifier") == "GoToWindow" else { try fail("CHOOSER_MISMATCH", "Expected the observed native Go To sheet") }
                try await focusApplication(observation)
                try perform(focused, action: "AXRaise")
                let current = try observe()
                guard let root = current.root,
                      let field = axElement(attribute(root, "AXFocusedUIElement")),
                      text(field, "AXIdentifier") == "PathTextField" else {
                    try fail("CHOOSER_MISMATCH", "The observed Go To field must remain focused before cancellation")
                }
                phase = "directory_cancel"
                try chooserKey(current, key: 53)
            } else {
                _ = try panel(observation, kind: kind)
                try perform(try node(observation, identifier: "CancelButton", role: "AXButton"))
            }
        } else { try fail("UNSUPPORTED_OPERATION", "Operation is not a supported Character Animator domain action") }
        let after = try observe()
        return ["state": after.state, "result": ["status": "native_command_dispatched", "completion": "unverified", "retry_safe": false]]
    }
}

NSApplication.shared.setActivationPolicy(.prohibited)
Task { @MainActor in
    var operation = "unknown", adapter: CharacterNative?
    do {
        let data = try FileHandle.standardInput.read(upToCount: 65537) ?? Data()
        guard data.count <= 65536, let request = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              request["version"] as? Int == 1, let requestedOperation = request["operation"] as? String,
              Set(request.keys).isSubset(of: ["version", "operation", "parameters", "expected", "application"]) else { try fail("INVALID_ARGUMENT", "Expected one version-1 JSON request of at most 64 KiB") }
        operation = requestedOperation
        adapter = try CharacterNative(applicationPath: request["application"] as? String ?? defaultApplication)
        let response = try await adapter!.run(request)
        var envelope = response; envelope["version"] = 1; envelope["ok"] = true; envelope["operation"] = operation
        FileHandle.standardOutput.write(try canonical(envelope)); FileHandle.standardOutput.write(Data([10]))
    } catch {
        let known = error as? NativeFailure
        let unknown = commandPaths[operation] != nil || operation == "project_select"
            ? adapter?.primaryCommandDispatched == true : adapter?.dispatched == true
        var errorEnvelope: [String: Any] = ["code": unknown ? "OUTCOME_UNKNOWN" : known?.code ?? "NATIVE_INSPECTION_FAILED",
            "native_code": known?.code ?? "NATIVE_INSPECTION_FAILED",
            "phase": adapter?.phase ?? "request_validation",
            "message": "[" + (adapter?.phase ?? "request_validation") + "] " + (known?.message ?? String(describing: error)),
            "outcome": unknown ? "unknown" : "rejected", "retry_safe": false]
        if operation == "continue_chooser" {
            errorEnvelope["submission_may_have_started"] = adapter?.submissionMayHaveStarted ?? false
            if let diagnostics = adapter?.continuationDiagnostics {
                errorEnvelope["continuation_diagnostics"] = diagnostics
            }
        }
        var envelope: [String: Any] = ["version": 1, "ok": false, "operation": operation,
                                       "error": errorEnvelope]
        if let state = adapter?.lastState { envelope["state"] = state }
        if let data = try? canonical(envelope) { FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data([10])) }
    }
    exit(0)
}
NSApplication.shared.run()
