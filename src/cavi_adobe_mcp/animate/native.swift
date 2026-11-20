// Bounded handler for Animate's per-file "Open JSFL File" execution chooser.
import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

let bundleID = "com.adobe.Adobe-Animate-2024.application"
struct NativeFailure: Error { let code: String; let message: String }
func fail(_ code: String, _ message: String) throws -> Never { throw NativeFailure(code: code, message: message) }
func attribute(_ element: AXUIElement, _ key: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, key as CFString, &value) == .success ? value : nil
}
func axElement(_ value: CFTypeRef?) -> AXUIElement? {
    guard let value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
    return unsafeBitCast(value, to: AXUIElement.self)
}
func string(_ element: AXUIElement, _ key: String) -> String? {
    (attribute(element, key) as? String).map { String($0.prefix(1024)) }
}
func children(_ element: AXUIElement) -> [AXUIElement] {
    attribute(element, "AXChildren") as? [AXUIElement] ?? []
}
func actions(_ element: AXUIElement) -> [String] {
    var names: CFArray?
    guard AXUIElementCopyActionNames(element, &names) == .success else { return [] }
    return names as? [String] ?? []
}
func enabled(_ element: AXUIElement) -> Bool {
    (attribute(element, "AXEnabled") as? NSNumber)?.boolValue ?? false
}
func selected(_ element: AXUIElement) -> Bool {
    if (attribute(element, "AXSelected") as? NSNumber)?.boolValue == true { return true }
    if let number = attribute(element, "AXValue") as? NSNumber { return number.intValue == 1 }
    if let value = attribute(element, "AXValue") as? String { return ["1", "true"].contains(value.lowercased()) }
    return false
}
func displayedText(_ element: AXUIElement) -> [String] {
    ["AXTitle", "AXDescription", "AXValue"].compactMap { key in
        string(element, key)
    }
}
func validateProcess(_ request: [String: Any]) throws -> NSRunningApplication {
    guard let pidValue = request["pid"] as? Int, pidValue > 0,
          let expectedStarted = request["started"] as? String, !expectedStarted.isEmpty,
          let expectedBundlePath = request["application"] as? String,
          let app = NSRunningApplication(processIdentifier: pid_t(pidValue)),
          app.bundleIdentifier == bundleID,
          app.bundleURL?.standardizedFileURL.path == expectedBundlePath else {
        try fail("APP_IDENTITY_MISMATCH", "Animate process identity changed before JSFL prompt handling")
    }
    let processes = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
    guard processes.count == 1, processes[0].processIdentifier == pid_t(pidValue) else {
        try fail("APP_AMBIGUOUS", "Animate process identity is ambiguous")
    }
    let ps = Process(), pipe = Pipe()
    ps.executableURL = URL(fileURLWithPath: "/bin/ps")
    ps.arguments = ["-p", String(pidValue), "-o", "pid=,lstart=,comm="]
    ps.standardOutput = pipe; ps.standardError = FileHandle.nullDevice
    try ps.run(); ps.waitUntilExit()
    let output = String(decoding: pipe.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        .split(whereSeparator: \.isWhitespace)
    guard ps.terminationStatus == 0, output.count >= 7,
          output.dropFirst().prefix(5).joined(separator: " ") == expectedStarted else {
        try fail("APP_IDENTITY_MISMATCH", "Animate launch time changed before JSFL prompt handling")
    }
    return app
}
func modalWindow(pid: pid_t) -> (id: CGWindowID, frame: CGRect, onscreen: Bool)? {
    let windows = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
    let candidates = windows.filter {
        $0[kCGWindowOwnerPID as String] as? Int == Int(pid)
            && $0[kCGWindowName as String] as? String == "Open JSFL File"
    }
    guard candidates.count <= 1, let window = candidates.first,
          let raw = window[kCGWindowNumber as String] as? NSNumber,
          let bounds = window[kCGWindowBounds as String] as? [String: Any],
          let frame = CGRect(dictionaryRepresentation: bounds as CFDictionary),
          frame.width >= 300, frame.height >= 100 else { return nil }
    return (CGWindowID(raw.uint32Value), frame, window[kCGWindowIsOnscreen as String] as? Bool == true)
}
func authenticationWindowVisible(pid: pid_t) -> Bool {
    let blockedTerms = ["sign in", "log in", "apple id", "apple account", "verification", "license",
                        "activation", "two-factor", "permission", "allow access"]
    let windows = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
    return windows.contains { window in
        guard window[kCGWindowOwnerPID as String] as? Int == Int(pid),
              window[kCGWindowIsOnscreen as String] as? Bool == true,
              let title = window[kCGWindowName as String] as? String else { return false }
        let normalized = title.lowercased()
        return blockedTerms.contains(where: normalized.contains)
    }
}
func observeWindow(_ root: AXUIElement, _ id: CGWindowID, _ frame: CGRect, _ pid: pid_t) throws -> AXUIElement {
    var hit: AXUIElement?
    guard AXUIElementCopyElementAtPosition(root, Float(frame.midX), Float(frame.midY), &hit) == .success else {
        try fail("WINDOW_AX_UNAVAILABLE", "Animate chooser is visible but its Accessibility controls are unavailable")
    }
    for _ in 0..<16 {
        guard let candidate = hit else { break }
        var candidatePID: pid_t = 0
        guard AXUIElementGetPid(candidate, &candidatePID) == .success, candidatePID == pid else { break }
        if string(candidate, "AXRole") == "AXWindow",
           string(candidate, "AXTitle") == "Open JSFL File" { return candidate }
        hit = axElement(attribute(candidate, "AXParent"))
    }
    try fail("WINDOW_AX_UNAVAILABLE", "Animate chooser did not resolve to its exact native window")
}
struct Entry { let element: AXUIElement; let role: String; let text: [String] }
func entries(_ root: AXUIElement) -> [Entry] {
    var result: [Entry] = [], budget = 512
    func walk(_ element: AXUIElement, depth: Int) {
        guard budget > 0 else { return }
        budget -= 1
        result.append(Entry(element: element, role: string(element, "AXRole") ?? "unknown", text: displayedText(element)))
        if depth < 10 {
            for child in children(element).prefix(96) { walk(child, depth: depth + 1) }
        }
    }
    walk(root, depth: 0)
    return result
}
func press(_ element: AXUIElement, _ label: String) throws {
    guard enabled(element), actions(element).contains("AXPress") else {
        try fail("CONTROL_UNAVAILABLE", "Observed " + label + " control is not enabled for AXPress")
    }
    let error = AXUIElementPerformAction(element, "AXPress" as CFString)
    guard error == .success else { try fail("CONTROL_ACTION_FAILED", "Animate did not acknowledge the " + label + " action") }
}
func run(_ request: [String: Any]) throws -> [String: Any] {
    guard request["operation"] as? String == "handle_jsfl_prompt",
          let operationID = request["operation_id"] as? String,
          operationID.range(of: "^[0-9a-f]{32}$", options: .regularExpression) != nil,
          let launcher = request["launcher"] as? String,
          launcher == "animate-\(operationID).jsfl",
          let wait = request["wait_seconds"] as? Double, wait >= 0, wait <= 3 else {
        try fail("INVALID_REQUEST", "JSFL prompt handler received an invalid internal operation")
    }
    let app = try validateProcess(request), pid = app.processIdentifier
    let deadline = Date().addingTimeInterval(wait)
    var observed: (id: CGWindowID, frame: CGRect, onscreen: Bool)?
    repeat {
        observed = modalWindow(pid: pid)
        if observed != nil { break }
        if authenticationWindowVisible(pid: pid) {
            try fail("PROMPT_BLOCKED", "Animate displayed an authentication, license, or permission prompt; no prompt was accepted")
        }
        Thread.sleep(forTimeInterval: 0.05)
    } while Date() < deadline
    guard let window = observed else { return ["status": "absent", "operation_id": operationID] }
    guard AXIsProcessTrusted() else {
        try fail("ACCESSIBILITY_UNAVAILABLE", "Animate JSFL chooser requires existing Accessibility access; no permission request was made")
    }
    let appRoot = AXUIElementCreateApplication(pid); AXUIElementSetMessagingTimeout(appRoot, 0.75)
    var settable = DarwinBoolean(false)
    guard AXUIElementIsAttributeSettable(appRoot, "AXFrontmost" as CFString, &settable) == .success,
          settable.boolValue,
          AXUIElementSetAttributeValue(appRoot, "AXFrontmost" as CFString, kCFBooleanTrue) == .success else {
        try fail("FOCUS_UNAVAILABLE", "Animate chooser cannot be focused through its observed Accessibility control")
    }
    let focusDeadline = Date().addingTimeInterval(1)
    while NSWorkspace.shared.frontmostApplication?.processIdentifier != pid && Date() < focusDeadline {
        Thread.sleep(forTimeInterval: 0.05)
    }
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else {
        try fail("FOCUS_CHANGED", "Animate did not become the verified frontmost process")
    }
    guard let focusedWindow = modalWindow(pid: pid), focusedWindow.id == window.id, focusedWindow.onscreen else {
        return ["status": "absent", "operation_id": operationID]
    }
    let nativeWindow = try observeWindow(appRoot, focusedWindow.id, focusedWindow.frame, pid)
    let observedEntries = entries(nativeWindow)
    let allText = observedEntries.flatMap(\.text).joined(separator: " ").lowercased()
    if observedEntries.contains(where: { $0.role == "AXSecureTextField" })
        || ["sign in", "log in", "apple id", "apple account", "verification code", "license expired",
            "activate your", "two-factor", "allow access", "permission"].contains(where: allText.contains) {
        try fail("PROMPT_BLOCKED", "Animate displayed an authentication, license, or permission prompt; no prompt was accepted")
    }
    guard allText.contains(launcher.lowercased()) else {
        try fail("LAUNCHER_MISMATCH", "Observed Animate chooser does not name this operation's JSFL launcher")
    }
    let radios = observedEntries.filter {
        ["AXRadioButton", "AXRadioGroup"].contains($0.role)
            && $0.text.contains(where: { $0.caseInsensitiveCompare("Run as command") == .orderedSame })
    }
    guard radios.count == 1, radios[0].role == "AXRadioButton" else {
        try fail("COMMAND_CHOICE_UNAVAILABLE", "The exact Run as command radio control is absent or ambiguous")
    }
    if !selected(radios[0].element) {
        try press(radios[0].element, "Run as command")
        Thread.sleep(forTimeInterval: 0.05)
        guard selected(radios[0].element) else {
            try fail("COMMAND_CHOICE_UNCONFIRMED", "Animate did not select Run as command")
        }
    }
    let buttons = observedEntries.filter {
        $0.role == "AXButton" && $0.text.contains(where: { $0.caseInsensitiveCompare("OK") == .orderedSame })
    }
    guard buttons.count == 1 else { try fail("OK_UNAVAILABLE", "The exact OK button is absent or ambiguous") }
    try press(buttons[0].element, "OK")
    return ["status": "submitted", "operation_id": operationID, "launcher": launcher,
            "window_id": Int(focusedWindow.id), "choice": "Run as command"]
}
let input = FileHandle.standardInput.readDataToEndOfFile()
var operation = "unknown"
do {
    let request = try JSONSerialization.jsonObject(with: input) as? [String: Any] ?? [:]
    operation = request["operation"] as? String ?? "unknown"
    let response: [String: Any] = ["version": 1, "operation": operation, "ok": true, "result": try run(request)]
    FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: response, options: [.sortedKeys]))
} catch let error as NativeFailure {
    let response: [String: Any] = ["version": 1, "operation": operation, "ok": false,
                                   "error": ["code": error.code, "message": error.message]]
    FileHandle.standardOutput.write((try? JSONSerialization.data(withJSONObject: response, options: [.sortedKeys])) ?? Data("{}".utf8))
} catch {
    let response: [String: Any] = ["version": 1, "operation": operation, "ok": false,
                                   "error": ["code": "NATIVE_ERROR", "message": String(describing: error)]]
    FileHandle.standardOutput.write((try? JSONSerialization.data(withJSONObject: response, options: [.sortedKeys])) ?? Data("{}".utf8))
}
