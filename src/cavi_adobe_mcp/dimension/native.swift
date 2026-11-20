// Bounded native Dimension observation, launch and PNG rendering.
import AppKit
import ApplicationServices
import CoreGraphics
import CryptoKit
import Foundation
import Vision

let bundleID = "com.adobe.dimension"
let appPath = "/Applications/Adobe Dimension/Adobe Dimension.app"
struct NativeFailure: Error {
    let code: String
    let message: String
    let outcome: String
    let retrySafe: Bool
    let phase: String?
    let renderDispatched: Bool?
    init(message: String, code: String = "NATIVE_FAILURE", outcome: String = "rejected", retrySafe: Bool = false,
         phase: String? = nil, renderDispatched: Bool? = nil) {
        self.code = code; self.message = message; self.outcome = outcome; self.retrySafe = retrySafe
        self.phase = phase; self.renderDispatched = renderDispatched
    }
}
func attribute(_ element: AXUIElement, _ key: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, key as CFString, &value) == .success ? value : nil
}
func element(_ value: CFTypeRef?) -> AXUIElement? {
    guard let value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
    return unsafeBitCast(value, to: AXUIElement.self)
}
func string(_ node: AXUIElement, _ key: String) -> String? {
    (attribute(node, key) as? String).map { String($0.prefix(2048)) }
}
func frame(_ node: AXUIElement) -> [String: Double]? {
    guard let rawPosition = attribute(node, "AXPosition"),
          let rawSize = attribute(node, "AXSize"),
          CFGetTypeID(rawPosition) == AXValueGetTypeID(),
          CFGetTypeID(rawSize) == AXValueGetTypeID() else { return nil }
    let positionValue = unsafeBitCast(rawPosition, to: AXValue.self)
    let sizeValue = unsafeBitCast(rawSize, to: AXValue.self)
    var position = CGPoint.zero
    var size = CGSize.zero
    guard AXValueGetValue(positionValue, .cgPoint, &position),
          AXValueGetValue(sizeValue, .cgSize, &size) else { return nil }
    return ["x": Double(position.x), "y": Double(position.y),
            "width": Double(size.width), "height": Double(size.height)]
}
func children(_ node: AXUIElement) -> [AXUIElement] { attribute(node, "AXChildren") as? [AXUIElement] ?? [] }
func actions(_ node: AXUIElement) -> [String] {
    var names: CFArray?
    guard AXUIElementCopyActionNames(node, &names) == .success else { return [] }
    return names as? [String] ?? []
}
func processIdentity(_ app: NSRunningApplication) -> [String: Any] {
    let bundlePath = app.bundleURL?.standardizedFileURL.path ?? ""
    let executablePath = app.executableURL?.standardizedFileURL.path ?? ""
    let launchTime = app.launchDate?.timeIntervalSince1970 ?? 0
    let facts: [String: Any] = ["bundle_id": bundleID, "pid": Int(app.processIdentifier),
                               "launch_time": launchTime, "bundle_path": bundlePath,
                               "executable_path": executablePath]
    let encoded = (try? JSONSerialization.data(withJSONObject: facts, options: [.sortedKeys])) ?? Data()
    return ["facts": facts, "sha256": SHA256.hash(data: encoded).map { String(format: "%02x", $0) }.joined()]
}
func node(_ item: AXUIElement, depth: Int, budget: inout Int) -> [String: Any] {
    budget -= 1
    var value: [String: Any] = ["role": string(item, "AXRole") ?? "unknown", "actions": actions(item)]
    for (key, output) in [("AXTitle", "title"), ("AXDescription", "description"),
                          ("AXIdentifier", "identifier"), ("AXDocument", "document_url")] {
        if let field = string(item, key) { value[output] = field }
    }
    if let bounds = frame(item) { value["frame"] = bounds }
    if value["role"] as? String != "AXSecureTextField", let field = string(item, "AXValue") { value["value"] = field }
    if let enabled = attribute(item, "AXEnabled") as? NSNumber { value["enabled"] = enabled.boolValue }
    if let selected = attribute(item, "AXSelected") as? NSNumber { value["selected"] = selected.boolValue }
    if let minimized = attribute(item, "AXMinimized") as? NSNumber { value["minimized"] = minimized.boolValue }
    if depth < 10 && budget > 0 {
        var descendants: [[String: Any]] = []
        for child in children(item).prefix(96) {
            guard budget > 0 else { break }
            descendants.append(node(child, depth: depth + 1, budget: &budget))
        }
        if !descendants.isEmpty { value["children"] = descendants }
    }
    return value
}
func hasAuthenticationPrompt(_ value: Any) -> Bool {
    if let items = value as? [Any] { return items.contains(where: hasAuthenticationPrompt) }
    guard let item = value as? [String: Any] else { return false }
    let role = item["role"] as? String ?? ""
    if role == "AXSecureTextField" { return true }
    let text = ((item["title"] as? String ?? "") + " " + (item["value"] as? String ?? "")).lowercased()
    let promptPhrases = ["sign in", "log in", "apple id", "apple account", "verification code", "license expired",
                         "activate your", "two-factor", "allow access"]
    if ["AXWindow", "AXSheet", "AXStaticText", "AXButton"].contains(role),
       promptPhrases.contains(where: { text.range(of: "(?<![a-z])\($0)(?![a-z])", options: .regularExpression) != nil }) { return true }
    return (item["children"] as? [Any] ?? []).contains(where: hasAuthenticationPrompt)
}
struct DimensionWindow {
    let ax: AXUIElement
    let frame: CGRect
    let id: CGWindowID
}
struct ScreenText {
    let rect: CGRect
    let text: String
    let confidence: Float
}
final class DispatchState { var dispatched = false; var renderDispatched = false; var phase = "preflight" }
func cgRect(_ item: [String: Any]) -> CGRect? {
    guard let bounds = item[kCGWindowBounds as String] as? NSDictionary else { return nil }
    return CGRect(dictionaryRepresentation: bounds as CFDictionary)
}
func cgWindowID(_ item: [String: Any]) -> CGWindowID? { item[kCGWindowNumber as String] as? CGWindowID }
func windowsOnScreen() -> [[String: Any]] {
    CGWindowListCopyWindowInfo([.optionOnScreenOnly], kCGNullWindowID) as? [[String: Any]] ?? []
}
func allWindows() -> [[String: Any]] {
    CGWindowListCopyWindowInfo([.optionAll], kCGNullWindowID) as? [[String: Any]] ?? []
}
func windowServerObservations(pid: pid_t) -> [[String: Any]] {
    windowsOnScreen().compactMap { item in
        guard (item[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid,
              let id = cgWindowID(item), let bounds = cgRect(item) else { return nil }
        var observation: [String: Any] = [
            "window_number": Int(id), "bounds": ["x": bounds.origin.x, "y": bounds.origin.y,
                "width": bounds.size.width, "height": bounds.size.height],
            "layer": item[kCGWindowLayer as String] as? Int ?? -1,
            "alpha": (item[kCGWindowAlpha as String] as? NSNumber)?.doubleValue ?? 1
        ]
        if let name = item[kCGWindowName as String] as? String { observation["name"] = name }
        if let owner = item[kCGWindowOwnerName as String] as? String { observation["owner_name"] = owner }
        return observation
    }
}
func uniqueElements(_ items: [AXUIElement]) -> [AXUIElement] {
    var result: [AXUIElement] = []
    for item in items where !result.contains(where: { CFEqual($0, item) }) { result.append(item) }
    return result
}
func descendants(_ root: AXUIElement, limit: Int = 4096) -> [AXUIElement] {
    var pending = [root], result: [AXUIElement] = [], budget = limit
    while let item = pending.popLast(), budget > 0 {
        budget -= 1
        if result.contains(where: { CFEqual($0, item) }) { continue }
        result.append(item)
        pending.append(contentsOf: children(item).prefix(128))
    }
    return result
}
func dimensionAXWindow(_ root: AXUIElement) throws -> AXUIElement {
    var windows = attribute(root, "AXWindows") as? [AXUIElement] ?? []
    if let focused = element(attribute(root, "AXFocusedWindow")), !windows.contains(where: { CFEqual($0, focused) }) {
        windows.append(focused)
    }
    windows = uniqueElements(windows.filter { string($0, "AXRole") == "AXWindow" && string($0, "AXTitle") == "Adobe Dimension" })
    guard windows.count == 1, frame(windows[0]) != nil else {
        throw NativeFailure(message: "The unique Dimension document window is unavailable", code: "WINDOW_UNAVAILABLE")
    }
    return windows[0]
}
func dimensionWindow(_ root: AXUIElement, pid: pid_t) throws -> DimensionWindow {
    let ax = try dimensionAXWindow(root)
    guard let bounds = frame(ax),
          let x = bounds["x"], let y = bounds["y"], let width = bounds["width"], let height = bounds["height"] else {
        throw NativeFailure(message: "The unique Dimension document window is unavailable", code: "WINDOW_UNAVAILABLE")
    }
    let windowFrame = CGRect(x: x, y: y, width: width, height: height)
    let matches = allWindows().filter {
        ($0[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid
            && ($0[kCGWindowName as String] as? String) == "Adobe Dimension" && cgRect($0) == windowFrame
    }
    guard matches.count == 1, let id = cgWindowID(matches[0]) else {
        throw NativeFailure(message: "The exact Dimension window is not uniquely visible", code: "WINDOW_UNAVAILABLE")
    }
    return DimensionWindow(ax: ax, frame: windowFrame, id: id)
}
func topmostWindow(at point: CGPoint, expected: CGWindowID) -> Bool {
    // Geometry includes transparent surfaces and windows on other Spaces.
    // Verify the actual Accessibility hit target and its owning native window.
    guard let target = allWindows().first(where: { cgWindowID($0) == expected }),
          let targetPID = (target[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value,
          let targetFrame = cgRect(target), targetFrame.contains(point) else { return false }
    var hit: AXUIElement?
    guard AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), Float(point.x), Float(point.y), &hit) == .success,
          let hit else { return false }
    var hitPID: pid_t = 0
    guard AXUIElementGetPid(hit, &hitPID) == .success, hitPID == targetPID else { return false }
    var current: AXUIElement? = hit
    for _ in 0..<12 {
        guard let node = current else { break }
        let candidates = [node, element(attribute(node, "AXWindow"))].compactMap { $0 }
        for candidate in candidates where ["AXWindow", "AXSheet"].contains(string(candidate, "AXRole")) {
            var owner: pid_t = 0
            guard AXUIElementGetPid(candidate, &owner) == .success, owner == targetPID else { continue }
            if let b = frame(candidate), CGRect(x: b["x"]!, y: b["y"]!, width: b["width"]!, height: b["height"]!) == targetFrame {
                return true
            }
        }
        current = element(attribute(node, "AXParent"))
    }
    return false
}
func capture(_ window: DimensionWindow, to url: URL) throws -> [ScreenText] {
    guard !FileManager.default.fileExists(atPath: url.path) else {
        throw NativeFailure(message: "Capture path already exists; refusing overwrite", code: "DESTINATION_EXISTS")
    }
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
    process.arguments = ["-l\(window.id)", "-o", "-x", url.path]
    try process.run(); process.waitUntilExit()
    guard process.terminationStatus == 0, let image = NSImage(contentsOf: url),
          var proposed = Optional(CGRect(origin: .zero, size: image.size)),
          let cgImage = image.cgImage(forProposedRect: &proposed, context: nil, hints: nil) else {
        throw NativeFailure(message: "Fresh Dimension window capture failed", code: "CAPTURE_FAILED")
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate; request.usesLanguageCorrection = false
    try VNImageRequestHandler(cgImage: cgImage).perform([request])
    var hits = (request.results ?? []).compactMap { observation -> ScreenText? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        return ScreenText(rect: observation.boundingBox,
                          text: candidate.string.trimmingCharacters(in: .whitespacesAndNewlines),
                          confidence: candidate.confidence)
    }
    // Full-window OCR can omit the small document title. Read that region at
    // its own scale and map its observations back to the window coordinates.
    if let titleImage = cgImage.cropping(to: CGRect(x: Double(cgImage.width) * 0.32, y: 0,
                                                   width: Double(cgImage.width) * 0.36,
                                                   height: Double(cgImage.height) * 0.06)) {
        let titleRequest = VNRecognizeTextRequest()
        titleRequest.recognitionLevel = .accurate; titleRequest.usesLanguageCorrection = false
        titleRequest.minimumTextHeight = 0.003
        try VNImageRequestHandler(cgImage: titleImage).perform([titleRequest])
        hits.removeAll { $0.rect.midX > 0.32 && $0.rect.midX < 0.68 && $0.rect.midY > 0.94 }
        for observation in titleRequest.results ?? [] {
            guard let candidate = observation.topCandidates(1).first else { continue }
            let r = observation.boundingBox
            hits.append(ScreenText(rect: CGRect(x: 0.32 + r.minX * 0.36, y: 0.94 + r.minY * 0.06,
                                                width: r.width * 0.36, height: r.height * 0.06),
                                   text: candidate.string.trimmingCharacters(in: .whitespacesAndNewlines),
                                   confidence: candidate.confidence))
        }
    }
    return hits
}
func screenshotText(_ hits: [ScreenText]) -> String {
    hits.map { $0.text.lowercased() }.joined(separator: "\n")
}
func containsAuthPrompt(_ hits: [ScreenText]) -> Bool {
    let patterns = ["apple id", "apple account", "verification code", "two-factor", "license expired",
                    "activate your", "allow access", "permission required", "sign in", "log in"]
    return hits.contains { hit in
        let line = hit.text.lowercased()
        return patterns.contains { line.range(of: "(?<![a-z])\($0)(?![a-z])", options: .regularExpression) != nil }
    }
}
func assertNoPrompt(_ root: AXUIElement, hits: [ScreenText]? = nil) throws {
    var observed: [Any] = []
    let windows = attribute(root, "AXWindows") as? [AXUIElement] ?? []
    var budget = 2048
    observed.append(contentsOf: windows.map { node($0, depth: 0, budget: &budget) })
    if let focused = element(attribute(root, "AXFocusedUIElement")) {
        observed.append(node(focused, depth: 0, budget: &budget))
    }
    if let bar = element(attribute(root, "AXMenuBar")) { observed.append(node(bar, depth: 0, budget: &budget)) }
    guard !hasAuthenticationPrompt(observed), hits.map(containsAuthPrompt) != true else {
        throw NativeFailure(message: "Authentication, license, or permission prompt observed; no action was taken", code: "AUTHENTICATION_BOUNDARY")
    }
}
func postFixedClick(point: CGPoint, pid: pid_t, window: CGWindowID, frame: CGRect,
                    renderDispatch: Bool = false, state: DispatchState) throws {
    let matches = allWindows().filter {
        cgWindowID($0) == window && ($0[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid
            && ($0[kCGWindowName as String] as? String) == "Adobe Dimension" && cgRect($0) == frame
    }
    let root = AXUIElementCreateApplication(pid)
    guard (attribute(root, "AXFrontmost") as? NSNumber)?.boolValue == true, matches.count == 1,
          topmostWindow(at: point, expected: window),
          let down = CGEvent(mouseEventSource: CGEventSource(stateID: .hidSystemState), mouseType: .leftMouseDown,
                             mouseCursorPosition: point, mouseButton: .left),
          let up = CGEvent(mouseEventSource: CGEventSource(stateID: .hidSystemState), mouseType: .leftMouseUp,
                           mouseCursorPosition: point, mouseButton: .left) else {
        let top = windowsOnScreen().first { row in
            cgRect(row)?.contains(point) == true && ((row[kCGWindowAlpha as String] as? NSNumber)?.doubleValue ?? 1) > 0.01
        }
        let owner = top?[kCGWindowOwnerName as String] as? String ?? "unavailable"
        let topID = top.flatMap(cgWindowID) ?? 0
        let layer = top?[kCGWindowLayer as String] as? Int ?? -1
        throw NativeFailure(message: "The exact Dimension window is not topmost at the fixed control; expected=\(window), top=\(topID), owner=\(owner), layer=\(layer), matches=\(matches.count), frontmost=\(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0), point=\(point)", code: "FOCUS_CHANGED")
    }
    down.setIntegerValueField(.mouseEventClickState, value: 1); up.setIntegerValueField(.mouseEventClickState, value: 1)
    down.flags = []; up.flags = []
    state.dispatched = true
    down.post(tap: .cghidEventTap); up.post(tap: .cghidEventTap)
    if renderDispatch { state.renderDispatched = true }
}
func fixedPoint(_ hit: ScreenText, frame: CGRect) -> CGPoint {
    CGPoint(x: frame.minX + hit.rect.midX * frame.width, y: frame.minY + (1 - hit.rect.midY) * frame.height)
}
func canonicalJSON(_ object: Any) throws -> Data {
    try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .fragmentsAllowed])
}
func absolutePath(_ raw: Any?, field: String) throws -> URL {
    guard let value = raw as? String, value.hasPrefix("/"), value.count < 4096,
          !value.unicodeScalars.contains(where: { $0.value < 32 }) else {
        throw NativeFailure(message: "\(field) must be an absolute local path", code: "INVALID_ARGUMENT")
    }
    let url = URL(fileURLWithPath: value).standardizedFileURL
    guard url.path == value, url.resolvingSymlinksInPath().path == value else {
        throw NativeFailure(message: "\(field) must be canonical and contain no symlink", code: "INVALID_PATH")
    }
    return url
}
func isContained(_ child: URL, in root: URL) -> Bool {
    child.pathComponents.count > root.pathComponents.count
        && child.pathComponents.starts(with: root.pathComponents)
}
func sha256(_ url: URL) throws -> String {
    let handle = try FileHandle(forReadingFrom: url)
    defer { try? handle.close() }
    var hasher = SHA256()
    while true {
        let data = try handle.read(upToCount: 1024 * 1024) ?? Data()
        if data.isEmpty { break }
        hasher.update(data: data)
    }
    return hasher.finalize().map { String(format: "%02x", $0) }.joined()
}
func perform(_ element: AXUIElement, action: String = "AXPress", state: DispatchState) throws {
    guard actions(element).contains(action), (attribute(element, "AXEnabled") as? NSNumber)?.boolValue != false else {
        throw NativeFailure(message: "Observed native control is unavailable", code: "CONTROL_UNAVAILABLE")
    }
    state.dispatched = true
    let status = AXUIElementPerformAction(element, action as CFString)
    guard status == .success else {
        throw NativeFailure(message: "Native action returned \(status.rawValue); inspect before any further action", code: "NATIVE_ACTION_UNKNOWN")
    }
}
func focusedPanel(_ root: AXUIElement, pid: pid_t) throws -> DimensionWindow {
    guard let ax = element(attribute(root, "AXFocusedWindow")),
          ["AXSheet", "AXWindow"].contains(string(ax, "AXRole")), let bounds = frame(ax),
          let x = bounds["x"], let y = bounds["y"], let width = bounds["width"], let height = bounds["height"] else {
        throw NativeFailure(message: "Expected a focused native folder chooser", code: "CHOOSER_UNAVAILABLE")
    }
    let rect = CGRect(x: x, y: y, width: width, height: height)
    let matches = allWindows().filter {
        ($0[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid && cgRect($0) == rect
    }
    guard matches.count == 1, let id = cgWindowID(matches[0]) else {
        throw NativeFailure(message: "The exact native folder chooser window is not visible", code: "CHOOSER_UNAVAILABLE")
    }
    return DimensionWindow(ax: ax, frame: rect, id: id)
}
func waitForPanel(_ root: AXUIElement, pid: pid_t) async throws -> DimensionWindow {
    for _ in 0..<8 {
        try assertNoPrompt(root)
        do { return try focusedPanel(root, pid: pid) }
        catch let failure as NativeFailure where failure.code == "CHOOSER_UNAVAILABLE" {
            try await Task.sleep(nanoseconds: 200_000_000)
        }
    }
    return try focusedPanel(root, pid: pid)
}
func setSaveFolder(root: AXUIElement, app: NSRunningApplication, processExpected: Any?,
                   directory: URL,
                   state: DispatchState) async throws -> [ScreenText] {
    try focusDocument(app, root: root, expected: processExpected, state: state)
    let (mainWindow, hits, settingsCapture) = try await waitForView(root: root, app: app, expected: processExpected,
        directory: directory, name: "export-ready", ready: renderSettingsVisible, interactive: { window, hits in
            guard let field = try? saveFolderField(hits) else { return false }
            return topmostWindow(at: fixedPoint(field, frame: window.frame), expected: window.id)
        })
    try FileManager.default.copyItem(at: settingsCapture, to: directory.appendingPathComponent("dimension-export-settings.png"))
    try verifyExportSettings(hits)
    let point = fixedPoint(try saveFolderField(hits), frame: mainWindow.frame)
    try assertNoPrompt(root, hits: hits)
    try postFixedClick(point: point, pid: app.processIdentifier, window: mainWindow.id, frame: mainWindow.frame, state: state)
    try await Task.sleep(nanoseconds: 250_000_000)
    try assertNoPrompt(root)
    var panel = try await waitForPanel(root, pid: app.processIdentifier)
    guard let focused = element(attribute(root, "AXFocusedWindow")), CFEqual(focused, panel.ax) else {
        throw NativeFailure(message: "The exact native folder chooser is not focused", code: "FOCUS_CHANGED")
    }
    let panelID = string(panel.ax, "AXIdentifier") ?? ""
    if panelID != "GoToWindow" {
        try pressFixedChord(keyCode: 5, flags: [.maskCommand, .maskShift], pid: app.processIdentifier,
                            window: panel, state: state)
        try await Task.sleep(nanoseconds: 250_000_000)
        try assertNoPrompt(root)
        panel = try await waitForPanel(root, pid: app.processIdentifier)
    }
    guard string(panel.ax, "AXIdentifier") == "GoToWindow" else {
        throw NativeFailure(message: "The native Go To Folder sheet did not appear", code: "GO_TO_FOLDER_UNAVAILABLE")
    }
    let pathFields = uniqueElements(descendants(panel.ax).filter {
        string($0, "AXIdentifier") == "PathTextField" && string($0, "AXRole") == "AXTextField"
            && actions($0).contains("AXConfirm")
    })
    guard pathFields.count == 1 else {
        throw NativeFailure(message: "The exact Go To Folder path field is absent or ambiguous", code: "GO_TO_FOLDER_UNAVAILABLE")
    }
    var pathSettable = DarwinBoolean(false)
    guard AXUIElementIsAttributeSettable(pathFields[0], "AXValue" as CFString, &pathSettable) == .success,
          pathSettable.boolValue else { throw NativeFailure(message: "Go To Folder path field is not writable", code: "GO_TO_FOLDER_UNAVAILABLE") }
    state.dispatched = true
    guard AXUIElementSetAttributeValue(pathFields[0], "AXFocused" as CFString, kCFBooleanTrue) == .success,
          AXUIElementSetAttributeValue(pathFields[0], "AXValue" as CFString, directory.path as CFString) == .success,
          string(pathFields[0], "AXValue") == directory.path else {
        throw NativeFailure(message: "Exact job directory was not entered in the native Go To field", code: "NATIVE_ACTION_UNKNOWN")
    }
    try pressFixedChord(keyCode: 36, flags: [], pid: app.processIdentifier, window: panel, state: state)
    try await Task.sleep(nanoseconds: 350_000_000)
    try assertNoPrompt(root)
    let chooser = try await waitForPanel(root, pid: app.processIdentifier)
    guard string(chooser.ax, "AXIdentifier") != "GoToWindow" else {
        throw NativeFailure(message: "Directory confirmation did not return to the native folder chooser", code: "GO_TO_FOLDER_UNKNOWN")
    }
    let nodes = descendants(chooser.ax)
    let selectedLocations = nodes.filter { item in
        let value = (attribute(item, "AXURL") as? URL)?.standardizedFileURL.path
            ?? (attribute(item, "AXURL") as? String).flatMap(URL.init(string:))?.standardizedFileURL.path
            ?? ""
        return value == directory.path
            || (string(item, "AXIdentifier") == "where popup"
                && string(item, "AXValue") == directory.lastPathComponent && value == directory.path)
    }
    guard !selectedLocations.isEmpty else {
        throw NativeFailure(message: "Native chooser did not expose the requested canonical directory", code: "DIRECTORY_NOT_OBSERVED")
    }
    let chooseButtons = uniqueElements(nodes.filter {
        string($0, "AXRole") == "AXButton" && string($0, "AXIdentifier") == "OKButton" && string($0, "AXTitle") == "Open"
            && (attribute($0, "AXEnabled") as? NSNumber)?.boolValue == true && actions($0).contains("AXPress")
    })
    guard chooseButtons.count == 1 else {
        throw NativeFailure(message: "The native folder chooser does not expose one enabled Open action", code: "CHOOSE_UNAVAILABLE")
    }
    try foreground(app, expected: processExpected)
    guard let focused = element(attribute(root, "AXFocusedWindow")), CFEqual(focused, chooser.ax) else {
        throw NativeFailure(message: "The exact native folder chooser is not focused before Choose", code: "FOCUS_CHANGED")
    }
    try assertNoPrompt(root)
    try perform(chooseButtons[0], state: state)
    let (_, after, settledCapture) = try await waitForView(root: root, app: app, expected: processExpected,
        directory: directory, name: "folder-ready", ready: renderSettingsVisible)
    try FileManager.default.copyItem(at: settledCapture, to: directory.appendingPathComponent("dimension-save-location.png"))
    try assertNoPrompt(root, hits: after)
    guard after.contains(where: { $0.text.localizedCaseInsensitiveContains(directory.lastPathComponent)
        && $0.rect.minX > 0.82 }) else {
        throw NativeFailure(message: "Render panel does not show the selected job folder", code: "DIRECTORY_NOT_OBSERVED")
    }
    return after
}
func expectedIdentity(_ value: Any?, app: NSRunningApplication) throws {
    let current = processIdentity(app)
    guard let expected = value as? [String: Any], Set(expected.keys) == Set(["facts", "sha256"]),
          let expectedFacts = expected["facts"] as? [String: Any],
          let currentFacts = current["facts"] as? [String: Any],
          expected["sha256"] as? String == current["sha256"] as? String,
          try canonicalJSON(expectedFacts) == canonicalJSON(currentFacts) else {
        throw NativeFailure(message: "Dimension process identity differs from the caller's current status observation", code: "STALE_PROCESS")
    }
}
@MainActor func launchDimension(_ request: [String: Any]) async throws -> [String: Any] {
    guard request["version"] as? Int == 1,
          Set(request.keys) == Set(["version", "operation"]) else {
        throw NativeFailure(message: "Expected a version-1 launch request with no caller-controlled parameters", code: "INVALID_ARGUMENT")
    }
    guard AXIsProcessTrusted() else {
        throw NativeFailure(message: "Existing Accessibility access is unavailable; no permission request was made", code: "PERMISSION_UNAVAILABLE")
    }
    let bundleURL = URL(fileURLWithPath: appPath).standardizedFileURL
    guard bundleURL.path == appPath,
          FileManager.default.fileExists(atPath: bundleURL.path),
          Bundle(url: bundleURL)?.bundleIdentifier == bundleID else {
        throw NativeFailure(message: "The fixed installed Adobe Dimension bundle failed identity validation", code: "APPLICATION_UNAVAILABLE")
    }
    let apps = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
    guard apps.count <= 1 else {
        throw NativeFailure(message: "Multiple Adobe Dimension processes are running; refusing launch", code: "APPLICATION_AMBIGUOUS")
    }
    let wasRunning = !apps.isEmpty
    var launchDispatched = false
    let app: NSRunningApplication
    if let existing = apps.first {
        guard existing.bundleURL?.standardizedFileURL.path == appPath else {
            throw NativeFailure(message: "The running Dimension process does not match the fixed installed bundle", code: "APPLICATION_IDENTITY_MISMATCH")
        }
        app = existing
    } else {
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        configuration.createsNewApplicationInstance = false
        configuration.promptsUserIfNeeded = false
        configuration.addsToRecentItems = false
        launchDispatched = true
        do {
            app = try await NSWorkspace.shared.openApplication(at: bundleURL, configuration: configuration)
        } catch {
            throw NativeFailure(message: "Dimension launch outcome is unknown: \(error)", code: "LAUNCH_OUTCOME_UNKNOWN",
                                outcome: "unknown", retrySafe: false, phase: "open_application")
        }
        guard app.bundleURL?.standardizedFileURL.path == appPath,
              app.bundleIdentifier == bundleID,
              app.processIdentifier > 0 else {
            throw NativeFailure(message: "Launch did not return the fixed installed Dimension process", code: "APPLICATION_IDENTITY_MISMATCH",
                                outcome: launchDispatched ? "unknown" : "rejected", retrySafe: false,
                                phase: "verify_launched_process")
        }
    }
    let currentApps = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
    guard currentApps.count == 1, let current = currentApps.first,
          current.processIdentifier == app.processIdentifier,
          current.bundleURL?.standardizedFileURL.path == appPath else {
        throw NativeFailure(message: "Dimension process identity changed during launch", code: "STALE_PROCESS",
                            outcome: launchDispatched ? "unknown" : "rejected", retrySafe: false,
                            phase: "verify_launched_process")
    }
    let root = AXUIElementCreateApplication(current.processIdentifier)
    AXUIElementSetMessagingTimeout(root, 0.75)
    do {
        try assertNoPrompt(root)
    } catch {
        throw NativeFailure(message: "Authentication, license, or permission prompt observed; no action was taken",
                            code: "AUTHENTICATION_BOUNDARY", outcome: launchDispatched ? "unknown" : "rejected",
                            retrySafe: false, phase: "inspect_launched_process")
    }
    return ["status": wasRunning ? "already_running" : "launched",
            "process_identity": processIdentity(current), "bundle_id": bundleID,
            "bundle_path": appPath, "launches_application": !wasRunning]
}
func menuCommand(_ root: AXUIElement, menuName: String) throws -> AXUIElement {
    guard let bar = element(attribute(root, "AXMenuBar")) else {
        throw NativeFailure(message: "Dimension menu bar is unavailable", code: "MENU_UNAVAILABLE")
    }
    let all = descendants(bar)
    let menus = all.filter { string($0, "AXRole") == "AXMenuBarItem" && string($0, "AXTitle") == menuName }
    guard menus.count == 1 else { throw NativeFailure(message: "Expected one observed \(menuName) menu", code: "MENU_UNAVAILABLE") }
    return menus[0]
}
func itemInMenu(_ menu: AXUIElement, title: String) throws -> AXUIElement {
    let all = descendants(menu)
    let items = all.filter { string($0, "AXRole") == "AXMenuItem" && string($0, "AXTitle") == title }
    guard items.count == 1 else { throw NativeFailure(message: "Expected one observed menu command \(title)", code: "MENU_COMMAND_UNAVAILABLE") }
    return items[0]
}
func foreground(_ app: NSRunningApplication, expected: Any?) throws {
    let root = AXUIElementCreateApplication(app.processIdentifier)
    guard (attribute(root, "AXFrontmost") as? NSNumber)?.boolValue == true else {
        throw NativeFailure(message: "Dimension is not the foreground application", code: "FOCUS_CHANGED")
    }
    try expectedIdentity(expected, app: app)
}
func focusDocument(_ app: NSRunningApplication, root: AXUIElement, expected: Any?, state: DispatchState) throws {
    try expectedIdentity(expected, app: app)
    try assertNoPrompt(root)
    state.dispatched = true
    guard AXUIElementSetAttributeValue(root, "AXFrontmost" as CFString, kCFBooleanTrue) == .success else {
        throw NativeFailure(message: "Dimension could not become the foreground app through Accessibility", code: "ACTIVATION_UNAVAILABLE")
    }
    try perform(dimensionAXWindow(root), action: "AXRaise", state: state)
    let docks = NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.dock")
    if docks.count == 1 {
        let dockRoot = AXUIElementCreateApplication(docks[0].processIdentifier)
        let items = descendants(dockRoot, limit: 3000).filter {
            string($0, "AXSubrole") == "AXApplicationDockItem"
                && (attribute($0, "AXURL") as? URL)?.standardizedFileURL.path == appPath
        }
        if items.count == 1 { try perform(items[0], state: state) }
    }
    _ = app.activate(options: [.activateAllWindows])
    CFRunLoopRunInMode(.defaultMode, 0.15, false)
}
func pressFixedChord(keyCode: CGKeyCode, flags: CGEventFlags, pid: pid_t,
                     window: DimensionWindow, state: DispatchState) throws {
    let root = AXUIElementCreateApplication(pid)
    guard (attribute(root, "AXFrontmost") as? NSNumber)?.boolValue == true,
          let focused = element(attribute(root, "AXFocusedWindow")), CFEqual(focused, window.ax),
          let down = CGEvent(keyboardEventSource: CGEventSource(stateID: .hidSystemState), virtualKey: keyCode, keyDown: true),
          let up = CGEvent(keyboardEventSource: CGEventSource(stateID: .hidSystemState), virtualKey: keyCode, keyDown: false) else {
        throw NativeFailure(message: "The exact Dimension chooser is not focused for the fixed keyboard command", code: "FOCUS_CHANGED")
    }
    down.flags = flags; up.flags = flags
    state.dispatched = true
    down.post(tap: .cghidEventTap); up.post(tap: .cghidEventTap)
}
func promptLine(_ hits: [ScreenText]) -> Bool { containsAuthPrompt(hits) }
func uniqueHit(_ hits: [ScreenText], _ predicate: (ScreenText) -> Bool, label: String) throws -> ScreenText {
    let found = hits.filter(predicate)
    guard found.count == 1 else { throw NativeFailure(message: "Expected one visible \(label) control", code: "CONTROL_UNAVAILABLE") }
    return found[0]
}
func renderSettingsVisible(_ hits: [ScreenText]) -> Bool {
    hits.contains { $0.text.caseInsensitiveCompare("Render Settings") == .orderedSame }
        && hits.contains { $0.text.caseInsensitiveCompare("Export settings") == .orderedSame }
}
func loading(_ hits: [ScreenText]) -> Bool {
    hits.contains { $0.text.lowercased().hasPrefix("loading") }
}
func waitForView(root: AXUIElement, app: NSRunningApplication, expected: Any?, directory: URL,
                 name: String, ready: ([ScreenText]) -> Bool,
                 interactive: ((DimensionWindow, [ScreenText]) -> Bool)? = nil) async throws -> (DimensionWindow, [ScreenText], URL) {
    // A bounded part of this foreground operation, never a background monitor.
    var wasReady = false
    var refocused = false
    for observation in 0..<16 {
        try await Task.sleep(nanoseconds: 500_000_000)
        try expectedIdentity(expected, app: app)
        try assertNoPrompt(root)
        let window: DimensionWindow
        do { window = try dimensionWindow(root, pid: app.processIdentifier) }
        catch let failure as NativeFailure where failure.code == "WINDOW_UNAVAILABLE" {
            wasReady = false
            continue
        }
        let url = directory.appendingPathComponent("dimension-\(name)-\(observation).png")
        let hits = try capture(window, to: url)
        try assertNoPrompt(root, hits: hits)
        let inputReady = interactive == nil || ((attribute(root, "AXFrontmost") as? NSNumber)?.boolValue == true
            && (interactive?(window, hits) ?? false))
        let isReady = !loading(hits) && ready(hits) && inputReady
        if isReady && wasReady { return (window, hits, url) }
        wasReady = isReady
        if !isReady && !loading(hits) && interactive != nil && !refocused {
            try focusDocument(app, root: root, expected: expected, state: DispatchState())
            refocused = true
        }
    }
    throw NativeFailure(message: "Dimension did not reach the required \(name) view within sixteen observations", code: "UI_NOT_READY")
}
func exportValue(_ hits: [ScreenText], label: String) throws -> ScreenText {
    let heading = try uniqueHit(hits, { $0.text.caseInsensitiveCompare(label) == .orderedSame
        && $0.rect.minX > 0.82 }, label: label)
    return try uniqueHit(hits, { $0.rect.minX > heading.rect.minX && $0.rect.minX < heading.rect.minX + 0.04
        && $0.rect.maxY < heading.rect.minY && heading.rect.minY - $0.rect.maxY < 0.035
        && $0.confidence >= 0.85 }, label: "\(label) field value")
}
func verifyExportSettings(_ hits: [ScreenText]) throws {
    // In the collapsed Format field the visible value is selected. An open
    // format menu also exposes PSD and must not be treated as selected PNG.
    let format = try exportValue(hits, label: "Format")
    let filename = try exportValue(hits, label: "Export filename")
    guard format.text == "PNG", filename.text == "render",
          !hits.contains(where: { $0.rect.minX > 0.82 && $0.text == "PSD" }) else {
        throw NativeFailure(message: "The collapsed Format field must select PNG and Export filename must be render", code: "EXPORT_SETTINGS_MISMATCH")
    }
}
func saveFolderField(_ hits: [ScreenText]) throws -> ScreenText {
    let label = try uniqueHit(hits, { $0.text.caseInsensitiveCompare("Save to") == .orderedSame
        && $0.confidence >= 0.85 && $0.rect.minX > 0.82 }, label: "Save to")
    return try uniqueHit(hits, { $0.confidence >= 0.75 && $0.rect.minX > 0.82 && $0.rect.maxY < label.rect.minY
        && label.rect.minY - $0.rect.maxY < 0.12 && $0.rect.width > 0.04 }, label: "Save to folder field")
}
func renderControl(_ hits: [ScreenText]) throws -> ScreenText {
    try uniqueHit(hits, { $0.text == "Render" && $0.confidence >= 0.9
        && $0.rect.minX > 0.9 && $0.rect.minY > 0.68 && $0.rect.maxY < 0.81 }, label: "Render")
}
func awaitCurrentApp(_ pid: pid_t, bundle: String, launch: TimeInterval) throws -> NSRunningApplication {
    let apps = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
    guard apps.count == 1, let app = apps.first, app.processIdentifier == pid,
          app.bundleURL?.standardizedFileURL.path == bundle,
          abs((app.launchDate?.timeIntervalSince1970 ?? 0) - launch) < 0.001 else {
        throw NativeFailure(message: "Dimension process identity changed during the native operation", code: "STALE_PROCESS")
    }
    return app
}
@MainActor func renderStart(_ request: [String: Any]) async throws -> [String: Any] {
    let state = DispatchState()
    do {
        guard request["version"] as? Int == 1,
              Set(request.keys) == Set(["version", "operation", "parameters", "expected_process_identity"]),
              let parameters = request["parameters"] as? [String: Any],
              Set(parameters.keys) == Set(["workspace", "source", "directory", "basename"]),
              parameters["basename"] as? String == "render" else {
            throw NativeFailure(message: "Expected a version-1 render_start request with fixed basename render", code: "INVALID_ARGUMENT")
        }
        guard AXIsProcessTrusted(), CGPreflightScreenCaptureAccess(), CGPreflightPostEventAccess() else {
            throw NativeFailure(message: "Required existing Accessibility, screen-capture, or event access is unavailable; no permission request was made", code: "PERMISSION_UNAVAILABLE")
        }
        let workspace = try absolutePath(parameters["workspace"], field: "workspace")
        let directory = try absolutePath(parameters["directory"], field: "directory")
        let source = try absolutePath(parameters["source"], field: "source")
        var isDirectory = ObjCBool(false)
        guard FileManager.default.fileExists(atPath: workspace.path, isDirectory: &isDirectory), isDirectory.boolValue,
              FileManager.default.fileExists(atPath: directory.path, isDirectory: &isDirectory), isDirectory.boolValue,
              isContained(directory, in: workspace), source.deletingLastPathComponent() == directory,
              source.pathExtension.lowercased() == "dn", source.deletingPathExtension().lastPathComponent == "render" else {
            throw NativeFailure(message: "Source must be the new workspace-owned render.dn inside its new job directory", code: "INVALID_PATH")
        }
        let sourceAttributes = try FileManager.default.attributesOfItem(atPath: source.path)
        let sourceSize = sourceAttributes[.size] as? NSNumber ?? 0
        guard sourceAttributes[.type] as? FileAttributeType == .typeRegular, sourceSize.intValue > 0,
              sourceSize.intValue <= 512 * 1024 * 1024 else {
            throw NativeFailure(message: "Owned Dimension scene is not a regular file within the 512 MiB bound", code: "INVALID_SOURCE")
        }
        let output = directory.appendingPathComponent("render.png")
        let beforeCapture = directory.appendingPathComponent("dimension-before.png")
        let afterCapture = directory.appendingPathComponent("dimension-after.png")
        guard !FileManager.default.fileExists(atPath: output.path),
              !FileManager.default.fileExists(atPath: directory.appendingPathComponent("render.psd").path),
              !FileManager.default.fileExists(atPath: beforeCapture.path),
              !FileManager.default.fileExists(atPath: afterCapture.path),
              !FileManager.default.fileExists(atPath: directory.appendingPathComponent("dimension-opened.png").path),
              !FileManager.default.fileExists(atPath: directory.appendingPathComponent("dimension-render-mode.png").path),
              !FileManager.default.fileExists(atPath: directory.appendingPathComponent("dimension-save-location.png").path) else {
            throw NativeFailure(message: "Render outputs and captures must be new paths", code: "DESTINATION_EXISTS")
        }
        let sourceHash = try sha256(source)
        let apps = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
        guard apps.count == 1, let app = apps.first,
              app.bundleURL?.standardizedFileURL.path == appPath,
              let launch = app.launchDate?.timeIntervalSince1970 else {
            throw NativeFailure(message: "One already-running installed Dimension process is required", code: "APPLICATION_UNAVAILABLE")
        }
        try expectedIdentity(request["expected_process_identity"], app: app)
        let pid = app.processIdentifier
        let root = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(root, 0.75)
        try assertNoPrompt(root)
        state.phase = "open_owned_copy"
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        configuration.createsNewApplicationInstance = false
        configuration.promptsUserIfNeeded = false
        configuration.addsToRecentItems = false
        state.dispatched = true
        let openedApp = try await NSWorkspace.shared.open([source], withApplicationAt: URL(fileURLWithPath: appPath), configuration: configuration)
        guard openedApp.processIdentifier == pid else {
            throw NativeFailure(message: "Open file handler did not use the verified Dimension process", code: "STALE_PROCESS")
        }
        try await Task.sleep(nanoseconds: 700_000_000)
        state.phase = "activate_owned_window"
        try focusDocument(app, root: root, expected: request["expected_process_identity"], state: state)
        try await Task.sleep(nanoseconds: 500_000_000)
        let afterOpenApp = try awaitCurrentApp(pid, bundle: appPath, launch: launch)
        try assertNoPrompt(root)
        state.phase = "document_ready"
        let (_, openedHits, openedURL) = try await waitForView(root: root, app: afterOpenApp,
            expected: request["expected_process_identity"], directory: directory, name: "document", ready: { hits in
                hits.contains { $0.confidence >= 0.85 && $0.rect.midX > 0.32 && $0.rect.midX < 0.68
                    && $0.rect.midY > 0.93 && $0.text == "render" }
            })
        var capturePaths = [openedURL.path]
        try assertNoPrompt(root, hits: openedHits)
        let documentTitle = openedHits.filter {
            $0.confidence >= 0.85 && $0.rect.midX > 0.32 && $0.rect.midX < 0.68 && $0.rect.midY > 0.93
                && $0.text.localizedCaseInsensitiveContains("render")
        }
        guard documentTitle.count == 1, !documentTitle[0].text.contains("*") else {
            throw NativeFailure(message: "The copied render.dn title or clean document state was not observed", code: "DOCUMENT_IDENTITY_UNAVAILABLE")
        }
        var renderHits = openedHits
        let renderMode = renderSettingsVisible(openedHits)
        if !renderMode {
            state.phase = "switch_render_mode"
            try focusDocument(app, root: root, expected: request["expected_process_identity"], state: state)
            let windowMenu = try menuCommand(root, menuName: "Window")
            try perform(windowMenu, state: state)
            try perform(itemInMenu(windowMenu, title: "Switch mode"), state: state)
            let (_, modeHits, modeCapture) = try await waitForView(root: root, app: afterOpenApp,
                expected: request["expected_process_identity"], directory: directory, name: "render-mode", ready: renderSettingsVisible)
            renderHits = modeHits
            capturePaths.append(modeCapture.path)
            try assertNoPrompt(root, hits: renderHits)
        }
        guard renderHits.contains(where: { $0.text.caseInsensitiveCompare("Render Settings") == .orderedSame }),
              renderHits.contains(where: { $0.text.caseInsensitiveCompare("Export settings") == .orderedSame }) else {
            throw NativeFailure(message: "Dimension did not reach its observed Render settings screen", code: "RENDER_MODE_UNAVAILABLE")
        }
        try verifyExportSettings(renderHits)
        state.phase = "save_to_folder"
        renderHits = try await setSaveFolder(root: root, app: app, processExpected: request["expected_process_identity"],
                                             directory: directory, state: state)
        capturePaths.append(directory.appendingPathComponent("dimension-export-settings.png").path)
        let folderCapture = directory.appendingPathComponent("dimension-save-location.png")
        capturePaths.append(folderCapture.path)
        try focusDocument(app, root: root, expected: request["expected_process_identity"], state: state)
        state.phase = "render_ready"
        let (finalWindow, finalHits, finalCapture) = try await waitForView(root: root, app: app,
            expected: request["expected_process_identity"], directory: directory, name: "render-ready", ready: renderSettingsVisible,
            interactive: { window, hits in
                guard let button = try? renderControl(hits) else { return false }
                return topmostWindow(at: fixedPoint(button, frame: window.frame), expected: window.id)
            })
        renderHits = finalHits
        try FileManager.default.copyItem(at: finalCapture, to: beforeCapture)
        try verifyExportSettings(renderHits)
        let renderButton = try renderControl(renderHits)
        let renderPoint = fixedPoint(renderButton, frame: finalWindow.frame)
        try foreground(try awaitCurrentApp(pid, bundle: appPath, launch: launch), expected: request["expected_process_identity"])
        try assertNoPrompt(root, hits: renderHits)
        state.phase = "render_click"
        try postFixedClick(point: renderPoint, pid: pid, window: finalWindow.id, frame: finalWindow.frame,
                           renderDispatch: true, state: state)
        try await Task.sleep(nanoseconds: 500_000_000)
        let afterApp = try awaitCurrentApp(pid, bundle: appPath, launch: launch)
        try foreground(afterApp, expected: request["expected_process_identity"])
        try assertNoPrompt(root)
        let afterWindow = try dimensionWindow(root, pid: pid)
        let afterHits = try capture(afterWindow, to: afterCapture)
        try assertNoPrompt(root, hits: afterHits)
        guard afterHits.contains(where: { $0.text.caseInsensitiveCompare("Render Status") == .orderedSame }),
              afterHits.contains(where: { $0.text.caseInsensitiveCompare("Cancel") == .orderedSame }) else {
            throw NativeFailure(message: "Render dispatch was not confirmed by the native Render Status panel", code: "RENDER_DISPATCH_UNCONFIRMED")
        }
        guard try sha256(source) == sourceHash else {
            throw NativeFailure(message: "Owned source scene changed during native render submission", code: "SOURCE_CHANGED")
        }
        return ["status": "render_requested", "output": output.path,
                "captures": capturePaths + [beforeCapture.path, afterCapture.path],
                "source_identity": "opened_owned_copy"]
    } catch let error as NativeFailure {
        throw NativeFailure(message: error.message, code: error.code,
                            outcome: state.renderDispatched ? "unknown" : "rejected", retrySafe: false,
                            phase: state.phase, renderDispatched: state.renderDispatched)
    } catch {
        throw NativeFailure(message: String(describing: error), code: "NATIVE_WORKFLOW_FAILURE",
                            outcome: state.renderDispatched ? "unknown" : "rejected", retrySafe: false,
                            phase: state.phase, renderDispatched: state.renderDispatched)
    }
}
@MainActor func run(_ request: [String: Any]) async throws -> [String: Any] {
    guard let operation = request["operation"] as? String else {
        throw NativeFailure(message: "Operation is required", code: "INVALID_ARGUMENT")
    }
    if operation == "launch" { return try await launchDimension(request) }
    if operation == "render_start" { return try await renderStart(request) }
    guard ["status", "inspect"].contains(operation) else {
        throw NativeFailure(message: "Unsupported operation", code: "UNSUPPORTED_OPERATION")
    }
    let apps = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
    guard apps.count <= 1 else { throw NativeFailure(message: "Multiple Adobe Dimension processes are running") }
    var result: [String: Any] = ["bundle_id": bundleID, "running": !apps.isEmpty,
        "accessibility_trusted": AXIsProcessTrusted(), "launches_application": false]
    guard let app = apps.first else { return result }
    result["pid"] = Int(app.processIdentifier)
    result["launch_time"] = app.launchDate?.timeIntervalSince1970 ?? 0
    result["process_identity"] = processIdentity(app)
    result["bundle_path"] = app.bundleURL?.standardizedFileURL.path ?? ""
    result["executable_path"] = app.executableURL?.standardizedFileURL.path ?? ""
    result["frontmost"] = app.isActive
    result["hidden"] = app.isHidden
    guard operation == "inspect", AXIsProcessTrusted() else { return result }
    result["window_server_windows"] = windowServerObservations(pid: app.processIdentifier)
    let root = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(root, 0.75)
    var budget = 768
    let focusedObservation: [String: Any]?
    if let focusedElement = element(attribute(root, "AXFocusedUIElement")) {
        focusedObservation = node(focusedElement, depth: 0, budget: &budget)
        result["focused_ui_element"] = focusedObservation
    } else { focusedObservation = nil }
    var rawWindows = attribute(root, "AXWindows") as? [AXUIElement] ?? []
    if let focused = element(attribute(root, "AXFocusedWindow")), !rawWindows.contains(where: { CFEqual($0, focused) }) {
        rawWindows.append(focused)
    }
    let menuBar = element(attribute(root, "AXMenuBar"))
    let menuObservation = menuBar.map { node($0, depth: 0, budget: &budget) }
    let windows = rawWindows.prefix(8).map { node($0, depth: 0, budget: &budget) }
    var observed: [Any] = windows
    if let menuObservation { observed.append(menuObservation) }
    if let focusedObservation { observed.append(focusedObservation) }
    let blocked = hasAuthenticationPrompt(observed)
    result["authentication_or_permission_prompt"] = blocked
    if blocked { result["manual_action_required"] = true }
    else {
        if let menuObservation { result["menu_bar"] = menuObservation }
        result["windows"] = windows
        if let focused = element(attribute(root, "AXFocusedWindow")) {
            var focusedBudget = 2048
            result["focused_window"] = node(focused, depth: 0, budget: &focusedBudget)
        }
    }
    return result
}
NSApplication.shared.setActivationPolicy(.prohibited)
Task { @MainActor in
    var operation = "unknown"
    do {
        let input = try FileHandle.standardInput.read(upToCount: 65537) ?? Data()
        guard input.count <= 65536,
              let request = try JSONSerialization.jsonObject(with: input) as? [String: Any] else {
            throw NativeFailure(message: "Expected one JSON request no larger than 64 KiB", code: "INVALID_ARGUMENT")
        }
        operation = request["operation"] as? String ?? "unknown"
        let result = try await run(request)
        let response: [String: Any] = ["version": 1, "operation": operation, "ok": true, "result": result]
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: response, options: [.sortedKeys, .fragmentsAllowed]))
    } catch {
        let native = error as? NativeFailure
        let errorPacket: Any
        if operation == "render_start" || operation == "launch" {
            var details: [String: Any] = ["code": native?.code ?? "NATIVE_WORKFLOW_FAILURE",
                "message": native?.message ?? String(describing: error),
                "outcome": native?.outcome ?? "rejected", "retry_safe": native?.retrySafe ?? false]
            if let phase = native?.phase { details["phase"] = phase }
            if operation == "render_start", let renderDispatched = native?.renderDispatched {
                details["render_dispatched"] = renderDispatched
            }
            errorPacket = details
        } else { errorPacket = native?.message ?? String(describing: error) }
        let response: [String: Any] = ["version": 1, "operation": operation, "ok": false, "error": errorPacket]
        FileHandle.standardOutput.write((try? JSONSerialization.data(withJSONObject: response, options: [.sortedKeys])) ?? Data("{}".utf8))
    }
    exit(0)
}
dispatchMain()
