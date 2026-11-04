/* Fixed Adobe Animate JSFL driver. Requests are JSON data, never caller code. */
function caviJson(value) {
    if (value === null || typeof value === "undefined") return "null";
    if (typeof value === "string") {
        var encoded = '"';
        for (var c = 0; c < value.length; c++) {
            var code = value.charCodeAt(c), character = value.charAt(c);
            if (code === 34) encoded += '\\"';
            else if (code === 92) encoded += "\\\\";
            else if (code === 8) encoded += "\\b";
            else if (code === 9) encoded += "\\t";
            else if (code === 10) encoded += "\\n";
            else if (code === 12) encoded += "\\f";
            else if (code === 13) encoded += "\\r";
            else if (code < 32) encoded += "\\u00" + (code < 16 ? "0" : "") + code.toString(16);
            else encoded += character;
        }
        return encoded + '"';
    }
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    var parts = [], key;
    if (value instanceof Array) {
        for (var i = 0; i < value.length; i++) parts.push(caviJson(value[i]));
        return "[" + parts.join(",") + "]";
    }
    for (key in value) if (value.hasOwnProperty(key)) parts.push(caviJson(key) + ":" + caviJson(value[key]));
    return "{" + parts.join(",") + "}";
}

function caviParseJSON(source) {
    var position = 0;
    function white() { while (/\s/.test(source.charAt(position)) && position < source.length) position++; }
    function string() {
        var result = "";
        if (source.charAt(position++) !== '"') throw new Error("Invalid JSON string");
        while (position < source.length) {
            var character = source.charAt(position++);
            if (character === '"') return result;
            if (character === "\\") {
                var escaped = source.charAt(position++);
                if (escaped === '"' || escaped === "\\" || escaped === "/") result += escaped;
                else if (escaped === "b") result += "\b";
                else if (escaped === "f") result += "\f";
                else if (escaped === "n") result += "\n";
                else if (escaped === "r") result += "\r";
                else if (escaped === "t") result += "\t";
                else if (escaped === "u") {
                    var hex = source.substr(position, 4);
                    if (!/^[0-9a-fA-F]{4}$/.test(hex)) throw new Error("Invalid JSON unicode escape");
                    result += String.fromCharCode(parseInt(hex, 16));
                    position += 4;
                } else throw new Error("Invalid JSON escape");
            } else {
                if (character.charCodeAt(0) < 32) throw new Error("Unescaped JSON control character");
                result += character;
            }
        }
        throw new Error("Unterminated JSON string");
    }
    function value() {
        white();
        var character = source.charAt(position), result, key;
        if (character === '"') return string();
        if (character === "[") {
            position++;
            result = [];
            white();
            if (source.charAt(position) === "]") { position++; return result; }
            while (true) {
                result.push(value());
                white();
                character = source.charAt(position++);
                if (character === "]") return result;
                if (character !== ",") throw new Error("Invalid JSON array");
            }
        }
        if (character === "{") {
            position++;
            result = {};
            white();
            if (source.charAt(position) === "}") { position++; return result; }
            while (true) {
                white();
                key = string();
                white();
                if (source.charAt(position++) !== ":") throw new Error("Invalid JSON object");
                result[key] = value();
                white();
                character = source.charAt(position++);
                if (character === "}") return result;
                if (character !== ",") throw new Error("Invalid JSON object");
            }
        }
        if (source.substr(position, 4) === "true") { position += 4; return true; }
        if (source.substr(position, 5) === "false") { position += 5; return false; }
        if (source.substr(position, 4) === "null") { position += 4; return null; }
        var number = source.substring(position).match(/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/);
        if (number) { position += number[0].length; return Number(number[0]); }
        throw new Error("Invalid JSON value");
    }
    var parsed = value();
    white();
    if (position !== source.length) throw new Error("Unexpected trailing JSON data");
    return parsed;
}

function caviNativePlatformPath(pathURI, volumeName) {
    try {
        var platformPath = FLfile.uriToPlatformPath(pathURI);
        var volumePrefix = "/" + volumeName + "/";
        if (platformPath && volumeName && platformPath.indexOf(volumePrefix) === 0)
            platformPath = platformPath.substring(volumePrefix.length - 1);
        return platformPath || null;
    } catch (error) {
        return null;
    }
}

function caviFindDocument(pathURI, volumeName) {
    for (var i = 0; i < fl.documents.length; i++) {
        if (caviSamePathURI(fl.documents[i].pathURI, pathURI, volumeName)) return fl.documents[i];
    }
    return null;
}

function caviCanonicalPathURI(pathURI, volumeName) {
    try {
        var platformPath = caviNativePlatformPath(pathURI, volumeName);
        var verified = platformPath ? FLfile.platformPathToURI(platformPath) : null;
        if (!platformPath || !verified || platformPath.charAt(0) !== "/") return pathURI;
        var parts = platformPath.split("/"), encoded = [];
        for (var i = 1; i < parts.length; i++) encoded.push(encodeURIComponent(parts[i]));
        return "file:///" + encoded.join("/");
    } catch (error) {
        return pathURI;
    }
}

function caviSamePathURI(left, right, volumeName) {
    if (left === right) return true;
    var leftPath = caviNativePlatformPath(left, volumeName);
    var rightPath = caviNativePlatformPath(right, volumeName);
    return !!leftPath && !!rightPath && leftPath === rightPath;
}

function caviInspectDocument(doc, volumeName) {
    var timeline = doc.getTimeline(), layers = [], symbols = [], items = doc.library.items || [];
    for (var i = 0; i < timeline.layers.length && i < 256; i++) {
        var layer = timeline.layers[i];
        var keys = [];
        for (var f = 0; f < layer.frameCount && f < 1000; f++) {
            var fr = layer.frames[f];
            if (fr.startFrame !== f) continue;
            var elements = [];
            for (var e = 0; e < fr.elements.length && e < 1024; e++) {
                var el = fr.elements[e];
                elements.push({index: e, type: el.elementType, matrix: el.matrix,
                               library_item: el.libraryItem ? el.libraryItem.name : null});
            }
            keys.push({frame: f, duration: fr.duration, tween: fr.tweenType, elements: elements});
        }
        layers.push({name: layer.name, type: layer.layerType, frame_count: layer.frameCount, keyframes: keys});
    }
    for (var j = 0; j < items.length && j < 1024; j++) {
        if (items[j].itemType === "movie clip" || items[j].itemType === "graphic" || items[j].itemType === "button")
            symbols.push({name: items[j].name, type: items[j].itemType});
    }
    return {path_uri: caviCanonicalPathURI(doc.pathURI, volumeName), native_path_uri: doc.pathURI,
        platform_path: caviNativePlatformPath(doc.pathURI, volumeName),
        modified_state: "not_exposed_by_documented_api",
        name: doc.name, width: doc.width, height: doc.height,
        frame_rate: doc.frameRate, frame_count: timeline.frameCount, layer_count: timeline.layerCount,
        layers: layers, symbols: symbols};
}

function caviCancelled(cancelURI) {
    if (cancelURI && FLfile.exists(cancelURI)) throw new Error("Operation cancelled at a safe boundary");
}

function caviRun(request, cancelURI) {
    if (!request || request.version !== 1 || typeof request.operation !== "string") throw new Error("Invalid request");
    caviCancelled(cancelURI);
    var volumeName = request.volume_name || "";
    var doc = request.document_uri ? caviFindDocument(request.document_uri, volumeName) : fl.getDocumentDOM();
    if (request.operation === "inspect") {
        var docs = [];
        for (var i = 0; i < fl.documents.length && i < 128; i++) docs.push(caviInspectDocument(fl.documents[i], volumeName));
        return {operation_id: request.operation_id, documents: docs, version: 1};
    }
    if (request.operation === "open_copy") {
        if (caviFindDocument(request.document_uri, volumeName)) throw new Error("Owned copy is already open");
        if (!fl.openDocument(request.document_uri)) throw new Error("Animate did not open the owned FLA copy");
        doc = caviFindDocument(request.document_uri, volumeName);
        if (!doc) throw new Error("Opened FLA path does not match the requested path");
        return {operation_id: request.operation_id, document: caviInspectDocument(doc, volumeName), version: 1};
    }
    if (request.operation === "create_import") {
        var created = fl.createDocument("timeline");
        if (!created) throw new Error("Animate could not create a timeline document");
        created.importFile(request.source_uri, false, false, false);
        if (!fl.saveDocument(created, request.staging_uri)) throw new Error("Animate could not save the staged editable FLA");
        if (!caviSamePathURI(created.pathURI, request.staging_uri, volumeName)) throw new Error("Staged FLA path does not match the requested path");
        return {operation_id: request.operation_id, document: caviInspectDocument(created, volumeName), version: 1};
    }
    if (request.operation === "publish_open") {
        var staged = caviFindDocument(request.staging_uri, volumeName);
        if (!staged || !caviSamePathURI(staged.pathURI, request.staging_uri, volumeName)) throw new Error("The exact staged FLA document is not open");
        fl.closeDocument(staged, false);
        if (caviFindDocument(request.staging_uri, volumeName)) throw new Error("Animate did not close the staged FLA document");
        if (!fl.openDocument(request.document_uri)) throw new Error("Animate could not open the published editable FLA");
        doc = caviFindDocument(request.document_uri, volumeName);
        if (!doc) throw new Error("Published FLA path does not match the requested path");
        return {operation_id: request.operation_id, document: caviInspectDocument(doc, volumeName), version: 1};
    }
    if (!doc || !caviSamePathURI(doc.pathURI, request.document_uri, volumeName)) throw new Error("The exact owned FLA document is not open");
    fl.setActiveWindow(doc);
    if (request.operation === "close_copy") {
        if (!fl.saveDocument(doc, request.document_uri)) throw new Error("Could not save the owned FLA before closing");
        fl.closeDocument(doc, false);
        if (caviFindDocument(request.document_uri, volumeName)) throw new Error("The exact owned FLA remains open");
        return {operation_id: request.operation_id, document: {path_uri: request.document_uri, closed: true}, version: 1};
    }
    if (request.operation === "set_stage_size") {
        if (request.width < 1 || request.width > 2880 || request.height < 1 || request.height > 2880)
            throw new Error("Requested stage dimensions are outside the supported range");
        caviCancelled(cancelURI);
        if (!doc.saveAsCopy(request.snapshot_uri) || !FLfile.exists(request.snapshot_uri))
            throw new Error("Animate could not preserve the pre-edit owned FLA snapshot");
        doc.width = request.width;
        doc.height = request.height;
        if (!fl.saveDocument(doc, request.document_uri)) throw new Error("Animate could not save the resized owned FLA");
        return {operation_id: request.operation_id, document: caviInspectDocument(doc, volumeName),
            snapshot_uri: request.snapshot_uri, version: 1};
    }
    if (request.operation === "edit_timeline") {
        var tl = doc.getTimeline(), priorFrame = tl.currentFrame, priorLayer = tl.currentLayer;
        if (!doc.saveAsCopy(request.snapshot_uri) || !FLfile.exists(request.snapshot_uri))
            throw new Error("Could not preserve the pre-edit FLA snapshot");
        try {
            for (var n = 0; n < request.edits.length; n++) {
                caviCancelled(cancelURI);
                var edit = request.edits[n], index = -1;
                for (var li = 0; li < tl.layers.length; li++) if (tl.layers[li].name === edit.layer) {
                    if (index !== -1) throw new Error("Layer name is ambiguous: " + edit.layer);
                    index = li;
                }
                if (edit.action === "add_layer") {
                    if (index !== -1) throw new Error("Layer name already exists");
                    tl.addNewLayer(edit.layer, "normal", true); continue;
                }
                if (index === -1) throw new Error("Layer is missing: " + edit.layer);
                tl.currentLayer = index;
                var selectedLayer = tl.layers[index];
                if (selectedLayer.locked || selectedLayer.layerType !== "normal") throw new Error("Layer must be editable and normal");
                tl.setSelectedLayers(index, true);
                if (edit.action === "duration") {
                    if (edit.count < selectedLayer.frameCount) throw new Error("Duration cannot discard authored frames");
                    if (edit.count > selectedLayer.frameCount)
                        tl.insertFrames(edit.count - selectedLayer.frameCount, false, selectedLayer.frameCount);
                    continue;
                }
                if (edit.frame >= selectedLayer.frameCount) throw new Error("Extend the layer before editing that frame");
                tl.currentFrame = edit.frame;
                if (edit.action === "keyframe") {
                    if (edit.blank) tl.convertToBlankKeyframes(edit.frame, edit.frame + 1);
                    else tl.convertToKeyframes(edit.frame, edit.frame + 1);
                    continue;
                }
                var frameData = selectedLayer.frames[edit.frame];
                if (frameData.startFrame !== edit.frame) throw new Error("Edit requires an authored keyframe");
                if (edit.action === "place_symbol") {
                    if (!doc.library.itemExists(edit.name)) throw new Error("Library symbol does not exist");
                    if (!doc.library.addItemToDocument({x: edit.x, y: edit.y}, edit.name)) throw new Error("Symbol placement failed");
                } else if (edit.action === "tween") {
                    if (edit.end >= selectedLayer.frameCount || selectedLayer.frames[edit.end].startFrame !== edit.end)
                        throw new Error("Tween requires two authored endpoint keyframes");
                    var endData = selectedLayer.frames[edit.end];
                    if (frameData.elements.length !== 1 || endData.elements.length !== 1 ||
                            !frameData.elements[0].libraryItem || !endData.elements[0].libraryItem ||
                            frameData.elements[0].libraryItem.name !== endData.elements[0].libraryItem.name)
                        throw new Error("Classic tween endpoints must contain one instance of the same symbol");
                    frameData.tweenType = "motion"; frameData.tweenEasing = edit.easing;
                } else {
                    if (edit.element >= frameData.elements.length) throw new Error("Element index is missing");
                    var element = frameData.elements[edit.element];
                    if (edit.action === "transform") {
                        var m = edit.matrix;
                        element.matrix = {a:m[0], b:m[1], c:m[2], d:m[3], tx:m[4], ty:m[5]};
                    } else if (edit.action === "symbol") {
                        if (doc.library.itemExists(edit.name)) throw new Error("Symbol name already exists");
                        doc.selection = [element];
                        doc.convertToSymbol(edit.symbol_type, edit.name, "top left");
                        if (!doc.library.itemExists(edit.name)) throw new Error("Symbol creation failed");
                    } else throw new Error("Unsupported timeline edit");
                }
            }
            if (!fl.saveDocument(doc, request.document_uri)) throw new Error("Edited FLA could not be saved");
        } finally {
            tl.currentFrame = Math.min(priorFrame, tl.frameCount - 1);
            tl.currentLayer = Math.min(priorLayer, tl.layers.length - 1);
        }
        return {operation_id: request.operation_id, document: caviInspectDocument(doc, volumeName),
                snapshot_uri: request.snapshot_uri, version: 1};
    }
    if (request.operation === "export_frames") {
        var timeline = doc.getTimeline(), count = timeline.frameCount, rate = doc.frameRate;
        if (count < 1 || count > 1000 || rate < 1 || rate > 120) throw new Error("Document frame bounds are unsupported");
        caviCancelled(cancelURI);
        if (!doc.saveAsCopy(request.snapshot_uri) || !FLfile.exists(request.snapshot_uri))
            throw new Error("Animate could not preserve the exact exported FLA snapshot");
        var current = timeline.currentFrame, outputs = [];
        try {
            for (var frame = 0; frame < count; frame++) {
                caviCancelled(cancelURI);
                timeline.currentFrame = frame;
                var uri = request.frame_uris[frame];
                if (!uri || !doc.exportPNG(uri, true, true)) throw new Error("Animate PNG export failed at frame " + (frame + 1));
                outputs.push({index: frame, path_uri: uri});
            }
        } finally {
            timeline.currentFrame = current;
        }
        return {operation_id: request.operation_id, document: caviInspectDocument(doc, volumeName),
            snapshot_uri: request.snapshot_uri, outputs: outputs, version: 1};
    }
    throw new Error("Unsupported native operation");
}

function caviDispatch(requestURI, responseURI, cancelURI) {
    var request = null, response, previousDocument = fl.getDocumentDOM();
    try {
        var raw = FLfile.read(requestURI);
        if (raw === null || raw === "") throw new Error("Request file is unavailable");
        request = caviParseJSON(raw);
        response = {version: 1, operation_id: request.operation_id, ok: true,
                    result: caviRun(request, cancelURI)};
    } catch (error) {
        response = {version: 1, operation_id: request ? request.operation_id : null,
                    ok: false, error: String(error)};
    }
    if (previousDocument) {
        for (var i = 0; i < fl.documents.length; i++) if (fl.documents[i] === previousDocument) {
            fl.setActiveWindow(previousDocument); break;
        }
    }
    if (!FLfile.write(responseURI, caviJson(response))) throw new Error("Could not write native response file");
    return caviJson(response);
}
