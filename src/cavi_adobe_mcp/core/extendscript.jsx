/* Shared native operations for the Photoshop and Illustrator drivers. No caller-supplied script is evaluated.
   The application driver follows this prelude and supplies CAVI_HOST: its document hooks and typed operations. */
var CAVI_UNSAFE = new RegExp('[\\\\"\\x00-\\x1f\\u2028\\u2029]', 'g');
function caviQuote(s) {
    return '"' + String(s).replace(CAVI_UNSAFE, function (c) {
        var map = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"};
        return map[c] || "\\u" + ("0000" + c.charCodeAt(0).toString(16)).slice(-4);
    }) + '"';
}
function caviJson(x) {
    if (x === null || x === undefined) return "null";
    if (typeof x === "string") return caviQuote(x);
    if (typeof x === "number" || typeof x === "boolean") return String(x);
    var a = [], k;
    if (x instanceof Array) {
        for (k = 0; k < x.length; k++) a.push(caviJson(x[k]));
        return "[" + a.join(",") + "]";
    }
    for (k in x) if (x.hasOwnProperty(k)) a.push(caviQuote(k) + ":" + caviJson(x[k]));
    return "{" + a.join(",") + "}";
}
function caviPath(d) { try { return d.fullName.fsName; } catch (_) { return null; } }
function caviOpen(d) {
    for (var i = 0; i < app.documents.length; i++) if (app.documents[i] == d) return true;
    return false;
}
function caviHas(table, key) { return table.hasOwnProperty(key); }

function caviRun(r, host) {
    // c is the state shared with the application hooks: the request, the changed boundary,
    // the temporary document to close, and the document helpers below.
    var c = {r: r, changed: false, temporary: null, item: null, targets: null, d: null, entry: null};
    var registry = $.global.__caviAdobeAuthoringV1;
    if (!registry) registry = $.global.__caviAdobeAuthoringV1 = {};
    c.registry = registry;
    var previous = app.documents.length ? app.activeDocument : null;
    var saved = host.capture();
    function layers(parent, prefix, budget) {
        var result = [];
        if (prefix.length > 32) throw Error("Layer nesting exceeds the supported limit");
        for (var i = 0; i < parent.layers.length; i++) {
            if (--budget.remaining < 0) throw Error("Document exceeds the 2048-layer inspection limit");
            var layer = parent.layers[i], p = prefix.concat([i]);
            var item = {path: p, name: layer.name, visible: layer.visible, opacity: layer.opacity,
                        kind: layer.typename, id: host.layerId(layer)};
            item.locked = host.locked(layer);
            host.layerFields(layer, item);
            result.push(item);
            if (host.isContainer(layer) && layer.layers.length)
                result = result.concat(layers(layer, p, budget));
        }
        return result;
    }
    function pathSnapshot(d) {
        var result = [];
        for (var n = 0; n < d.pathItems.length && n < 2048; n++) {
            var pathItem = d.pathItems[n], subpaths = host.subpaths(pathItem), subs = [];
            for (var j = 0; j < subpaths.length; j++) {
                var points = [];
                for (var k = 0; k < subpaths[j].pathPoints.length; k++) {
                    var p = subpaths[j].pathPoints[k];
                    points.push({anchor: p.anchor, left: p.leftDirection, right: p.rightDirection});
                }
                subs.push({closed: subpaths[j].closed, points: points});
            }
            result.push({name: pathItem.name, subpaths: subs});
        }
        return result;
    }
    function snapshot(d) {
        var before = app.documents.length ? app.activeDocument : null;
        try {
            host.activate(d);
            return snapshotData(d);
        } finally {
            host.reactivate(before);
        }
    }
    function snapshotData(d) {
        var profile = null;
        try { profile = d.colorProfileName; } catch (_) {}
        var identity = host.identity(d), size = host.size(d), color = host.color(d);
        return {name: d.name, path: caviPath(d), native_id: identity.native_id,
                identity: identity.identity, saved: d.saved, width: size.width,
                height: size.height, units: size.units, color_profile: profile,
                color_space: color.color_space, bit_depth: color.bit_depth,
                layers: layers(d, [], {remaining: 2048}), paths: pathSnapshot(d)};
    }
    function layerAt(d, p, expected) {
        var node = d;
        for (var i = 0; i < p.length; i++) {
            host.requireContainer(node, d);
            if (!node.layers || p[i] >= node.layers.length) throw Error("Layer path no longer exists");
            node = node.layers[p[i]];
        }
        if (node.name !== expected) throw Error("Layer name changed; inspect the exact path again");
        return node;
    }
    function layerIdentity(d, target) {
        function find(parent, prefix) {
            for (var n = 0; n < parent.layers.length; n++) {
                var node = parent.layers[n], p = prefix.concat([n]);
                if (node == target) return {path: p, name: node.name, native_id: host.layerId(node)};
                if (host.isContainer(node) && node.layers.length) {
                    var found = find(node, p); if (found) return found;
                }
            }
            return null;
        }
        var found = find(d, []);
        if (!found) throw Error("Changed layer identity could not be observed");
        return found;
    }
    function groupAt(d) {
        if (r.group_path === null || r.group_path === undefined) return d;
        var group = layerAt(d, r.group_path, r.expected_group_name);
        host.requireGroup(group);
        if (host.locked(group)) throw Error("Target group is locked");
        return group;
    }
    function owned() {
        var entry = registry[r.session_id];
        if (!entry) entry = host.rebind(c);
        if (!entry || entry.token !== r.ownership_token || !caviOpen(entry.document)
                || caviPath(entry.document) !== r.document_path || entry.path !== r.document_path)
            throw Error("Exact copied document ownership is unavailable; inspect app and session");
        var current = snapshot(entry.document);
        if (r.action !== "inspect_recovery" && r.action !== "adopt_recovery" && caviJson(current) !== entry.observed) throw Error("Working document changed outside this session");
        return entry;
    }
    function rgbDocument(d) {
        if (!host.isRgb(d)) throw Error("This operation requires an RGB document");
    }
    function saveOwned(d) {
        // The copied file is the only save target. Source documents are never selected or saved.
        d.save();
        if (caviPath(d) !== r.document_path) throw Error("Working document path changed during save");
    }
    c.layers = layers; c.snapshot = snapshot; c.layerAt = layerAt; c.layerIdentity = layerIdentity;
    c.groupAt = groupAt; c.rgbDocument = rgbDocument; c.saveOwned = saveOwned;
    try {
        host.enter();
        var result, entry, d, i;
        if (r.action === "fonts") {
            result = {fonts: host.fonts()};
        } else if (r.action === "inspect") {
            if (app.documents.length > 64) throw Error("More than 64 documents are open; inspect manually");
            var documents = [];
            for (i = 0; i < app.documents.length; i++) documents.push(snapshot(app.documents[i]));
            result = {version: app.version, documents: documents};
        } else if (r.action === "open_copy") {
            for (i = 0; i < app.documents.length; i++)
                if (caviPath(app.documents[i]) === r.document_path) throw Error("Copy is already open and is not owned");
            if (registry[r.session_id]) throw Error("Session already has native ownership");
            var file = new File(r.input_copy), target = new File(r.document_path);
            if (!file.exists || !target.exists) throw Error("Source copy and exclusive destination reservation are required");
            if (!target.open("r")) throw Error("Destination reservation cannot be read");
            var reserved = target.read(); target.close();
            if (reserved !== "CAVI_ADOBE_RESERVED:" + r.destination_reservation)
                throw Error("Destination is no longer exclusively owned by this operation");
            c.changed = true;
            d = app.open(file);
            if (caviPath(d) !== file.fsName) throw Error("Opened source copy identity differs from its staging path");
            host.saveCopy(d, target);
            if (caviPath(d) !== r.document_path) throw Error("Opened document identity differs from copied source");
            result = snapshot(d);
            registry[r.session_id] = {token: r.ownership_token, path: r.document_path,
                                     document: d, observed: caviJson(result)};
        } else if (caviHas(host.initial, r.action)) {
            result = host.initial[r.action](c);
        } else {
            entry = owned(); d = entry.document;
            c.entry = entry; c.d = d;
            app.activeDocument = d;
            if (r.action === "inspect_session" || r.action === "inspect_recovery") result = snapshot(d);
            else if (r.action === "adopt_recovery") {
                var currentRecovery = snapshot(d);
                if (!d.saved || caviJson(currentRecovery) !== caviJson(r.expected_recovery_snapshot))
                    throw Error("Recovery snapshot changed or the owned copy has unsaved edits");
                c.changed = true; entry.observed = caviJson(currentRecovery); result = currentRecovery;
            }
            else if (r.action === "close_copy") {
                // A user edit fails owned() first. Closing never saves undispatched changes.
                c.changed = true;
                d.close(SaveOptions.DONOTSAVECHANGES);
                delete registry[r.session_id];
                result = {closed: true, path: r.document_path};
            } else if (caviHas(host.direct, r.action)) {
                result = host.direct[r.action](c);
            } else if (r.action === "export") {
                var output = new File(r.destination);
                if (output.exists || !output.parent.exists) throw Error("Export needs a new destination");
                var profile = host.exportDocument(c, output);
                result = snapshot(d); result.export_profile = profile;
                result.export_path = output.fsName;
            } else {
                if (!caviHas(host.ops, r.action)) throw Error("Unsupported typed operation");
                var op = host.ops[r.action];
                op.check(c);
                c.changed = true;
                app.activeDocument = d;
                op.run(c);
                saveOwned(d);
                result = snapshot(d);
                result.changed_layers = [];
                if (c.targets) {
                    for (i = 0; i < c.targets.length; i++) result.changed_layers.push(layerIdentity(d, c.targets[i]));
                } else if (c.item) result.changed_layers.push(layerIdentity(d, c.item));
            }
            if (r.action !== "close_copy" && r.action !== "inspect_session" && r.action !== "inspect_recovery") entry.observed = caviJson(snapshot(d));
        }
        return caviJson({ok: true, operation_id: r.operation_id, result: result});
    } catch (error) {
        return caviJson({ok: false, operation_id: r.operation_id,
                         outcome: c.changed ? "unknown" : "rejected", error: String(error)});
    } finally {
        // Only temporary documents created by this command may be closed here.
        if (c.temporary) { try { c.temporary.close(SaveOptions.DONOTSAVECHANGES); } catch (_) {} }
        if (previous && caviOpen(previous)) { try { app.activeDocument = previous; } catch (_) {} }
        host.leave(saved);
    }
}

function caviPublish(r, response) {
    if (r.completion_path) {
        var completionFile = new File(r.completion_path + ".tmp");
        completionFile.encoding = "UTF-8";
        if (!completionFile.open("w")) throw Error("Cannot retain the native completion receipt");
        completionFile.write(response); completionFile.close();
        if (!completionFile.rename(new File(r.completion_path).name))
            throw Error("Cannot publish the native completion receipt");
    }
}
