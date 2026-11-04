/* Fixed native operations for adobe_authoring.py. No caller-supplied script is evaluated. */
var CAVI_RESPONSE = (function (r) {
    function quote(s) {
        return '"' + String(s).replace(/[\\"\x00-\x1f\u2028\u2029]/g, function (c) {
            var map = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"};
            return map[c] || "\\u" + ("0000" + c.charCodeAt(0).toString(16)).slice(-4);
        }) + '"';
    }
    function json(x) {
        if (x === null || x === undefined) return "null";
        if (typeof x === "string") return quote(x);
        if (typeof x === "number" || typeof x === "boolean") return String(x);
        var a = [], k;
        if (x instanceof Array) {
            for (k = 0; k < x.length; k++) a.push(json(x[k]));
            return "[" + a.join(",") + "]";
        }
        for (k in x) if (x.hasOwnProperty(k)) a.push(quote(k) + ":" + json(x[k]));
        return "{" + a.join(",") + "}";
    }
    var ps = r.app === "photoshop", changed = false;
    var registry = $.global.__caviAdobeAuthoringV1;
    if (!registry) registry = $.global.__caviAdobeAuthoringV1 = {};
    var previous = app.documents.length ? app.activeDocument : null;
    var dialogs = ps ? app.displayDialogs : app.userInteractionLevel;
    var units = ps ? app.preferences.rulerUnits : null;
    var temporary = null;
    function path(d) { try { return d.fullName.fsName; } catch (_) { return null; } }
    function open(d) {
        for (var i = 0; i < app.documents.length; i++) if (app.documents[i] == d) return true;
        return false;
    }
    function layers(parent, prefix, budget) {
        var result = [];
        if (prefix.length > 32) throw Error("Layer nesting exceeds the supported limit");
        for (var i = 0; i < parent.layers.length; i++) {
            if (--budget.remaining < 0) throw Error("Document exceeds the 2048-layer inspection limit");
            var layer = parent.layers[i], p = prefix.concat([i]);
            var item = {path: p, name: layer.name, visible: layer.visible, opacity: layer.opacity,
                        kind: layer.typename, id: ps ? layer.id : null};
            item.locked = ps ? layer.allLocked : layer.locked;
            if (ps && layer.typename === "ArtLayer" && layer.kind === LayerKind.TEXT) {
                item.text = {contents: layer.textItem.contents, font: layer.textItem.font,
                             size: layer.textItem.size.as("pt")};
            }
            if (ps) {
                var maskRef = new ActionReference(); maskRef.putIdentifier(charIDToTypeID("Lyr "), layer.id);
                var maskDesc = executeActionGet(maskRef);
                item.has_user_mask = maskDesc.hasKey(stringIDToTypeID("hasUserMask")) && maskDesc.getBoolean(stringIDToTypeID("hasUserMask"));
            } else {
                item.text = [];
                function collectText(parent) {
                    for (var t = 0; t < parent.pageItems.length; t++) {
                        var page = parent.pageItems[t];
                        if (page.parent !== parent) continue;
                        if (page.typename === "TextFrame") item.text.push({contents: page.contents,
                            font: page.textRange.characterAttributes.textFont.name,
                            size: page.textRange.characterAttributes.size});
                        else if (page.typename === "GroupItem") collectText(page);
                    }
                }
                collectText(layer);
                item.clipping_groups = 0;
                for (var g = 0; g < layer.groupItems.length; g++) if (layer.groupItems[g].clipped) item.clipping_groups++;
            }
            result.push(item);
            if ((!ps || layer.typename === "LayerSet") && layer.layers.length)
                result = result.concat(layers(layer, p, budget));
        }
        return result;
    }
    function pathSnapshot(d) {
        var result = [];
        for (var n = 0; n < d.pathItems.length && n < 2048; n++) {
            var pathItem = d.pathItems[n], subpaths = ps ? pathItem.subPathItems : [pathItem], subs = [];
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
            if (ps) app.activeDocument = d;
            return snapshotData(d);
        } finally {
            if (ps && before && open(before)) app.activeDocument = before;
        }
    }
    function snapshotData(d) {
        var profile = null;
        try { profile = d.colorProfileName; } catch (_) {}
        return {name: d.name, path: path(d), native_id: ps ? d.id : null,
                identity: ps ? "document_id_and_retained_reference" : "retained_document_reference_and_path",
                saved: d.saved, width: ps ? d.width.as("px") : d.width,
                height: ps ? d.height.as("px") : d.height,
                units: ps ? "pixels" : "points", color_profile: profile,
                color_space: String(ps ? d.mode : d.documentColorSpace),
                bit_depth: ps ? String(d.bitsPerChannel) : null,
                layers: layers(d, [], {remaining: 2048}), paths: pathSnapshot(d)};
    }
    function layerAt(d, p, expected) {
        var node = d;
        for (var i = 0; i < p.length; i++) {
            if (ps && node !== d && node.typename !== "LayerSet") throw Error("Layer path is not a group");
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
                if (node == target) return {path: p, name: node.name, native_id: ps ? node.id : null};
                if ((!ps || node.typename === "LayerSet") && node.layers.length) {
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
        if (ps && group.typename !== "LayerSet") throw Error("Target is not a layer group");
        if (ps ? group.allLocked : group.locked) throw Error("Target group is locked");
        return group;
    }
    function owned() {
        var entry = registry[r.session_id];
        // Photoshop ignores #targetengine, so AppleEvent scripts cannot keep a DOM reference
        // in $.global. Rebind only the exact same live document id/path and require the last
        // server-journaled snapshot to match before permitting any owned-copy operation.
        if (ps && !entry && r.expected_document && r.expected_document.native_id !== null) {
            for (var n = 0; n < app.documents.length; n++) {
                var candidate = app.documents[n];
                if (candidate.id === r.expected_document.native_id
                        && path(candidate) === r.document_path) {
                    entry = {token: r.ownership_token, path: r.document_path,
                             document: candidate, observed: json(r.expected_document)};
                    registry[r.session_id] = entry;
                    break;
                }
            }
        }
        if (!entry || entry.token !== r.ownership_token || !open(entry.document)
                || path(entry.document) !== r.document_path || entry.path !== r.document_path)
            throw Error("Exact copied document ownership is unavailable; inspect app and session");
        var current = snapshot(entry.document);
        if (r.action !== "inspect_recovery" && r.action !== "adopt_recovery" && json(current) !== entry.observed) throw Error("Working document changed outside this session");
        return entry;
    }
    function rgbDocument(d) {
        if (ps ? d.mode !== DocumentMode.RGB : d.documentColorSpace !== DocumentColorSpace.RGB)
            throw Error("This operation requires an RGB document");
    }
    function saveOwned(d) {
        // The copied file is the only save target. Source documents are never selected or saved.
        d.save();
        if (path(d) !== r.document_path) throw Error("Working document path changed during save");
    }
    try {
        if (ps) { app.displayDialogs = DialogModes.NO; app.preferences.rulerUnits = Units.PIXELS; }
        else app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
        var result, entry, d, i;
        if (r.action === "fonts") {
            var fonts = [], collection = ps ? app.fonts : app.textFonts;
            for (i = 0; i < collection.length; i++) {
                var font = collection[i];
                fonts.push({name: font.name, family: font.family, style: font.style,
                            postscript_name: ps ? font.postScriptName : font.name});
            }
            result = {fonts: fonts};
        } else if (r.action === "inspect") {
            if (app.documents.length > 64) throw Error("More than 64 documents are open; inspect manually");
            var documents = [];
            for (i = 0; i < app.documents.length; i++) documents.push(snapshot(app.documents[i]));
            result = {version: app.version, documents: documents};
        } else if (r.action === "open_copy") {
            for (i = 0; i < app.documents.length; i++)
                if (path(app.documents[i]) === r.document_path) throw Error("Copy is already open and is not owned");
            if (registry[r.session_id]) throw Error("Session already has native ownership");
            var file = new File(r.input_copy), target = new File(r.document_path);
            if (!file.exists || !target.exists) throw Error("Source copy and exclusive destination reservation are required");
            if (!target.open("r")) throw Error("Destination reservation cannot be read");
            var reserved = target.read(); target.close();
            if (reserved !== "CAVI_ADOBE_RESERVED:" + r.destination_reservation)
                throw Error("Destination is no longer exclusively owned by this operation");
            changed = true;
            d = app.open(file);
            if (path(d) !== file.fsName) throw Error("Opened source copy identity differs from its staging path");
            if (ps) {
                var psd = new PhotoshopSaveOptions(); psd.layers = true; psd.embedColorProfile = true;
                d.saveAs(target, psd, false, Extension.LOWERCASE);
            } else {
                var ai = new IllustratorSaveOptions(); ai.pdfCompatible = true; ai.embedICCProfile = true;
                d.saveAs(target, ai);
            }
            if (path(d) !== r.document_path) throw Error("Opened document identity differs from copied source");
            result = snapshot(d);
            registry[r.session_id] = {token: r.ownership_token, path: r.document_path,
                                     document: d, observed: json(result)};
        } else if (r.action === "create_canvas") {
            if (!ps) throw Error("Canvas creation is supported only in Photoshop");
            if (r.expected_document !== null && r.expected_document !== undefined)
                throw Error("Canvas creation requires a new session without an existing native document");
            if (registry[r.session_id]) throw Error("Session already has native ownership");
            if (typeof r.canvas_width !== "number" || !isFinite(r.canvas_width)
                    || Math.floor(r.canvas_width) !== r.canvas_width || r.canvas_width < 1 || r.canvas_width > 2880
                    || typeof r.canvas_height !== "number" || !isFinite(r.canvas_height)
                    || Math.floor(r.canvas_height) !== r.canvas_height || r.canvas_height < 1 || r.canvas_height > 2880)
                throw Error("Canvas width and height must be integers in 1..2880");
            if (typeof r.session_id !== "string" || !/^[0-9a-f]{32}$/.test(r.session_id)
                    || typeof r.ownership_token !== "string" || !/^[0-9a-f]{32}$/.test(r.ownership_token)
                    || typeof r.destination_reservation !== "string" || !/^[0-9a-f]{32}$/.test(r.destination_reservation))
                throw Error("Canvas ownership and reservation tokens are invalid");
            var recipeFile = new File(r.input_copy), destination = new File(r.document_path);
            if (!recipeFile.exists || recipeFile.name.toLowerCase().slice(-5) !== ".json"
                    || recipeFile.length > 4096 || !destination.exists
                    || destination.name.slice(-4) !== ".psd"
                    || !destination.parent.exists)
                throw Error("Canvas recipe and exclusive PSD reservation are required");
            if (!destination.open("r")) throw Error("Canvas reservation cannot be read");
            var reservationContents = destination.read(); destination.close();
            if (reservationContents !== "CAVI_ADOBE_RESERVED:" + r.destination_reservation)
                throw Error("Canvas destination is no longer exclusively owned by this operation");
            recipeFile.encoding = "UTF-8";
            if (!recipeFile.open("r")) throw Error("Retained canvas recipe cannot be read");
            var recipeContents = recipeFile.read(); recipeFile.close();
            if (r.canvas_bit_depth !== 8 && r.canvas_bit_depth !== 16) throw Error("Canvas depth must be 8 or 16");
            var canvasDepth = r.canvas_bit_depth === 16 ? BitsPerChannelType.SIXTEEN : BitsPerChannelType.EIGHT;
            var expectedRecipe = "{\"background\":\"transparent\",\"bit_depth\":" + r.canvas_bit_depth + ","
                + "\"color_profile\":\"sRGB IEC61966-2.1\",\"height\":" + r.canvas_height
                + ",\"kind\":\"photoshop_canvas_recipe\",\"mode\":\"RGB\",\"resolution\":72,"
                + "\"version\":1,\"width\":" + r.canvas_width + "}";
            if (recipeContents !== expectedRecipe)
                throw Error("Retained canvas recipe does not match the transparent RGB sRGB request");
            for (i = 0; i < app.documents.length; i++)
                if (path(app.documents[i]) === destination.fsName) throw Error("Canvas destination is already open");

            changed = true;
            d = app.documents.add(new UnitValue(r.canvas_width, "px"), new UnitValue(r.canvas_height, "px"),
                                  72, destination.name.replace(/\.psd$/i, ""), NewDocumentMode.RGB,
                                  DocumentFill.TRANSPARENT, 1, canvasDepth,
                                  "sRGB IEC61966-2.1");
            if (!d || !open(d) || d.width.as("px") !== r.canvas_width || d.height.as("px") !== r.canvas_height
                    || d.resolution !== 72 || d.mode !== DocumentMode.RGB
                    || d.bitsPerChannel !== canvasDepth
                    || d.colorProfileName !== "sRGB IEC61966-2.1"
                    || d.layers.length !== 1 || d.layers[0].isBackgroundLayer !== false)
                throw Error("Photoshop did not create the requested transparent RGB sRGB canvas");
            app.activeDocument = d;
            var canvasOptions = new PhotoshopSaveOptions();
            canvasOptions.layers = true; canvasOptions.embedColorProfile = true;
            d.saveAs(destination, canvasOptions, false, Extension.LOWERCASE);
            if (path(d) !== r.document_path || !destination.exists || destination.length <= 0)
                throw Error("Saved canvas does not match its reserved PSD destination");
            result = snapshot(d);
            result.resolution = d.resolution;
            result.canvas_background = "transparent";
            result.has_background_layer = d.layers[0].isBackgroundLayer;
            registry[r.session_id] = {token: r.ownership_token, path: r.document_path,
                                     document: d, observed: json(snapshot(d))};
        } else {
            entry = owned(); d = entry.document;
            app.activeDocument = d;
            if (r.action === "inspect_session" || r.action === "inspect_recovery") result = snapshot(d);
            else if (r.action === "adopt_recovery") {
                var currentRecovery = snapshot(d);
                if (!d.saved || json(currentRecovery) !== json(r.expected_recovery_snapshot))
                    throw Error("Recovery snapshot changed or the owned copy has unsaved edits");
                changed = true; entry.observed = json(currentRecovery); result = currentRecovery;
            }
            else if (r.action === "close_copy") {
                // A user edit fails owned() first. Closing never saves undispatched changes.
                changed = true;
                d.close(SaveOptions.DONOTSAVECHANGES);
                delete registry[r.session_id];
                result = {closed: true, path: r.document_path};
            } else if (r.action === "export") {
                var output = new File(r.destination), profile = null;
                if (output.exists || !output.parent.exists) throw Error("Export needs a new destination");
                if (r.kind === "png" && ps) {
                    if (d.bitsPerChannel !== BitsPerChannelType.EIGHT && d.bitsPerChannel !== BitsPerChannelType.SIXTEEN)
                        throw Error("PNG export preserves 8/16-bit documents; convert 32-bit sources explicitly");
                    if (r.usage === "color") {
                        rgbDocument(d);
                        if (d.colorProfileType === ColorProfile.NONE)
                            throw Error("Color PNG requires a tagged RGB source document");
                    }
                    else if (d.mode !== DocumentMode.RGB && d.mode !== DocumentMode.GRAYSCALE)
                        throw Error("Data PNG requires RGB or grayscale; conversion is not implicit");
                    changed = true;
                    temporary = d.duplicate("Cavi PNG export " + r.operation_id, false);
                    if (r.usage === "color") {
                        temporary.convertProfile("sRGB IEC61966-2.1", Intent.RELATIVECOLORIMETRIC, true, false);
                        profile = temporary.colorProfileName;
                    } else temporary.colorProfileType = ColorProfile.NONE;
                    var png = new PNGSaveOptions(); png.interlaced = false; png.compression = 6;
                    temporary.saveAs(output, png, true, Extension.LOWERCASE);
                    temporary.close(SaveOptions.DONOTSAVECHANGES); temporary = null;
                } else if (!ps) {
                    rgbDocument(d);
                    if (d.artboards.length !== 1) throw Error("Exports require exactly one artboard");
                    if (r.usage === "color" && d.colorProfileName !== "sRGB IEC61966-2.1")
                        throw Error("Illustrator export requires an sRGB document; conversion is not implicit");
                    profile = r.usage === "color" ? d.colorProfileName : null;
                    changed = true;
                    if (r.kind === "png") {
                        var aiPng = new ExportOptionsPNG24();
                        aiPng.antiAliasing = r.usage === "color"; aiPng.transparency = true;
                        aiPng.artBoardClipping = true; aiPng.horizontalScale = 100; aiPng.verticalScale = 100;
                        d.exportFile(output, ExportType.PNG24, aiPng);
                    } else if (r.kind === "svg" && r.usage === "color") {
                        var svg = new ExportOptionsSVG();
                        svg.embedRasterImages = true; svg.fontType = SVGFontType.OUTLINEFONT;
                        svg.preserveEditability = false; svg.coordinatePrecision = 4;
                        d.exportFile(output, ExportType.SVG, svg);
                    } else throw Error("Unsupported export");
                } else throw Error("Photoshop SVG export is unsupported");
                result = snapshot(d); result.export_profile = profile;
                result.export_path = output.fsName;
            } else {
                var targets = [], item, group;
                if (r.action === "edit_layers") {
                    for (i = 0; i < r.edits.length; i++) {
                        item = layerAt(d, r.edits[i].path, r.edits[i].expected_name);
                        if (ps ? item.allLocked : item.locked) throw Error("A target layer is locked");
                        targets.push(item);
                    }
                } else if (r.action === "add_rectangle") {
                    rgbDocument(d);
                    var b = r.bounds;
                    var width = ps ? d.width.as("px") : d.width, height = ps ? d.height.as("px") : d.height;
                    if (b[0] + b[2] > width || b[1] + b[3] > height) throw Error("Rectangle exceeds document bounds");
                } else if (r.action === "place_image" || r.action === "add_group") {
                    group = groupAt(d);
                    if (r.action === "place_image") {
                        rgbDocument(d);
                        if (!(new File(r.source)).exists) throw Error("Staged PNG does not exist");
                    }
                } else if (r.action === "add_path" || r.action === "add_text") {
                    rgbDocument(d);
                    if (r.action === "add_path") group = groupAt(d);
                    if (r.action === "add_text") {
                        var chosen = null, fontList = ps ? app.fonts : app.textFonts;
                        for (i = 0; i < fontList.length; i++)
                            if ((ps ? fontList[i].postScriptName : fontList[i].name) === r.font) chosen = fontList[i];
                        if (!chosen) throw Error("Exact PostScript font is unavailable; use fonts discovery");
                    } else if (ps) {
                        for (i = 0; i < d.pathItems.length; i++)
                            if (d.pathItems[i].name === r.name) throw Error("Path name already exists");
                    }
                } else if (r.action === "apply_mask") {
                    item = layerAt(d, r.layer_path, r.expected_name);
                    if (ps ? item.allLocked : item.locked) throw Error("Mask target is locked");
                    var maskPath = null, matches = 0;
                    for (i = 0; i < d.pathItems.length; i++)
                        if (d.pathItems[i].name === r.path_name) { maskPath = d.pathItems[i]; matches++; }
                    if (matches !== 1 || (!ps && !maskPath.closed) ||
                            (ps && (maskPath.subPathItems.length !== 1 || !maskPath.subPathItems[0].closed))) throw Error("Mask requires one exact closed path");
                    if (ps) {
                        app.activeDocument = d; d.activeLayer = item;
                        var layerRef = new ActionReference(); layerRef.putIdentifier(charIDToTypeID("Lyr "), item.id);
                        var layerDesc = executeActionGet(layerRef);
                        if (layerDesc.hasKey(stringIDToTypeID("hasUserMask")) && layerDesc.getBoolean(stringIDToTypeID("hasUserMask")))
                            throw Error("Layer already has a mask");
                    } else if (item.pageItems.length === 0) throw Error("Clip layer contains no artwork");
                } else throw Error("Unsupported typed operation");
                changed = true;
                app.activeDocument = d;
                if (r.action === "edit_layers") {
                    for (i = 0; i < targets.length; i++) {
                        var edit = r.edits[i]; item = targets[i];
                        if (edit.name !== undefined) item.name = edit.name;
                        if (edit.visible !== undefined) item.visible = edit.visible;
                        if (edit.opacity !== undefined) item.opacity = edit.opacity;
                    }
                } else if (r.action === "add_group") {
                    item = ps ? group.layerSets.add() : group.layers.add(); item.name = r.name;
                } else if (r.action === "add_rectangle") {
                    if (ps) {
                        item = d.artLayers.add(); item.name = r.name;
                        d.selection.select([[b[0], b[1]], [b[0] + b[2], b[1]],
                                            [b[0] + b[2], b[1] + b[3]], [b[0], b[1] + b[3]]]);
                        var color = new SolidColor(); color.rgb.red = r.rgb[0];
                        color.rgb.green = r.rgb[1]; color.rgb.blue = r.rgb[2];
                        d.selection.fill(color, ColorBlendMode.NORMAL, 100, false); d.selection.deselect();
                    } else {
                        var board = d.artboards[0].artboardRect;
                        var layer = d.layers.add(); layer.name = r.name;
                        item = layer.pathItems.rectangle(board[1] - b[1], board[0] + b[0], b[2], b[3]);
                        var fill = new RGBColor(); fill.red = r.rgb[0]; fill.green = r.rgb[1]; fill.blue = r.rgb[2];
                        item.fillColor = fill; item.stroked = false; item = layer;
                    }
                    item.opacity = r.opacity;
                } else if (r.action === "place_image") {
                    if (ps) {
                        temporary = app.open(new File(r.source));
                        item = temporary.activeLayer.duplicate(d, ElementPlacement.PLACEATBEGINNING);
                        temporary.close(SaveOptions.DONOTSAVECHANGES); temporary = null;
                        app.activeDocument = d; item.name = r.name;
                        if (group !== d) item.move(group, ElementPlacement.INSIDE);
                        item.translate(r.position[0] - item.bounds[0].as("px"),
                                       r.position[1] - item.bounds[1].as("px"));
                    } else {
                        var placed = d.placedItems.add(); placed.file = new File(r.source);
                        var artboard = d.artboards[0].artboardRect;
                        placed.position = [artboard[0] + r.position[0], artboard[1] - r.position[1]];
                        var targetLayer = group === d ? d.layers.add() : group.layers.add();
                        targetLayer.name = r.name;
                        placed.move(targetLayer, ElementPlacement.PLACEATBEGINNING);
                        placed.embed(); item = targetLayer;
                    }
                }
                if (r.action === "add_path") {
                    if (ps) {
                        var sub = new SubPathInfo(), pts = [];
                        sub.closed = r.closed; sub.operation = ShapeOperation.SHAPEADD;
                        for (i = 0; i < r.points.length; i++) {
                            var pp = new PathPointInfo(); pp.kind = PointKind.CORNERPOINT;
                            pp.anchor = r.points[i].anchor; pp.leftDirection = r.points[i].left;
                            pp.rightDirection = r.points[i].right; pts.push(pp);
                        }
                        sub.entireSubPath = pts;
                        var authoredPath = d.pathItems.add(r.name, [sub]);
                        if (r.rgb !== null) {
                            item = group.artLayers.add(); item.name = r.name;
                            var pathColor = new SolidColor(); pathColor.rgb.red = r.rgb[0];
                            pathColor.rgb.green = r.rgb[1]; pathColor.rgb.blue = r.rgb[2];
                            authoredPath.fillPath(pathColor, ColorBlendMode.NORMAL, 100, false, 0, true);
                        }
                    } else {
                        item = group.layers.add(); item.name = r.name;
                        authoredPath = item.pathItems.add(); authoredPath.name = r.name;
                        var boardPath = d.artboards[0].artboardRect;
                        function mapped(pair) { return [boardPath[0] + pair[0], boardPath[1] - pair[1]]; }
                        for (i = 0; i < r.points.length; i++) {
                            var aiPoint = authoredPath.pathPoints.add();
                            aiPoint.anchor = mapped(r.points[i].anchor); aiPoint.leftDirection = mapped(r.points[i].left);
                            aiPoint.rightDirection = mapped(r.points[i].right); aiPoint.pointType = PointType.CORNER;
                        }
                        authoredPath.closed = r.closed; authoredPath.stroked = false; authoredPath.filled = r.rgb !== null;
                        if (r.rgb !== null) {
                            var pathFill = new RGBColor(); pathFill.red = r.rgb[0]; pathFill.green = r.rgb[1]; pathFill.blue = r.rgb[2];
                            authoredPath.fillColor = pathFill;
                        }
                    }
                } else if (r.action === "add_text") {
                    if (ps) {
                        item = d.artLayers.add(); item.kind = LayerKind.TEXT; item.name = r.name;
                        var textItem = item.textItem; textItem.contents = r.text; textItem.font = r.font;
                        textItem.position = r.position; textItem.size = new UnitValue(r.size, "pt");
                        var textColor = new SolidColor(); textColor.rgb.red = r.rgb[0]; textColor.rgb.green = r.rgb[1]; textColor.rgb.blue = r.rgb[2];
                        textItem.color = textColor;
                        if (textItem.font !== r.font) throw Error("Native text substituted the requested font: " + textItem.font);
                    } else {
                        item = d.layers.add(); item.name = r.name;
                        var textFrame = item.textFrames.add(); textFrame.contents = r.text;
                        var tb = d.artboards[0].artboardRect;
                        textFrame.position = [tb[0] + r.position[0], tb[1] - r.position[1]];
                        textFrame.textRange.characterAttributes.textFont = chosen;
                        textFrame.textRange.characterAttributes.size = r.size;
                        var textFill = new RGBColor(); textFill.red = r.rgb[0]; textFill.green = r.rgb[1]; textFill.blue = r.rgb[2];
                        textFrame.textRange.characterAttributes.fillColor = textFill;
                    }
                } else if (r.action === "apply_mask") {
                    if (ps) {
                        d.activeLayer = item;
                        maskPath.makeSelection(0, true, SelectionType.REPLACE);
                        var md = new ActionDescriptor(), mr = new ActionReference();
                        md.putClass(charIDToTypeID("Nw  "), charIDToTypeID("Chnl"));
                        mr.putEnumerated(charIDToTypeID("Chnl"), charIDToTypeID("Chnl"), charIDToTypeID("Msk "));
                        md.putReference(charIDToTypeID("At  "), mr);
                        md.putEnumerated(charIDToTypeID("Usng"), charIDToTypeID("UsrM"), charIDToTypeID("RvlS"));
                        executeAction(charIDToTypeID("Mk  "), md, DialogModes.NO); d.selection.deselect();
                    } else {
                        var clipGroup = item.groupItems.add(), direct = [];
                        for (i = 0; i < item.pageItems.length; i++)
                            if (item.pageItems[i] !== clipGroup && item.pageItems[i].parent === item) direct.push(item.pageItems[i]);
                        for (i = 0; i < direct.length; i++) direct[i].move(clipGroup, ElementPlacement.PLACEATEND);
                        var clip = maskPath.duplicate(clipGroup, ElementPlacement.PLACEATBEGINNING);
                        clip.clipping = true; clipGroup.clipped = true;
                    }
                }
                saveOwned(d);
                result = snapshot(d);
                result.changed_layers = [];
                if (r.action === "edit_layers") {
                    for (i = 0; i < targets.length; i++) result.changed_layers.push(layerIdentity(d, targets[i]));
                } else if (item) result.changed_layers.push(layerIdentity(d, item));
            }
            if (r.action !== "close_copy" && r.action !== "inspect_session" && r.action !== "inspect_recovery") entry.observed = json(snapshot(d));
        }
        return json({ok: true, operation_id: r.operation_id, result: result});
    } catch (error) {
        return json({ok: false, operation_id: r.operation_id,
                     outcome: changed ? "unknown" : "rejected", error: String(error)});
    } finally {
        // Only temporary documents created by this command may be closed here.
        if (temporary) { try { temporary.close(SaveOptions.DONOTSAVECHANGES); } catch (_) {} }
        if (previous && open(previous)) { try { app.activeDocument = previous; } catch (_) {} }
        if (ps) { app.displayDialogs = dialogs; app.preferences.rulerUnits = units; }
        else app.userInteractionLevel = dialogs;
    }
})(CAVI_REQUEST);

if (CAVI_REQUEST.completion_path) {
    var completionFile = new File(CAVI_REQUEST.completion_path + ".tmp");
    completionFile.encoding = "UTF-8";
    if (!completionFile.open("w")) throw Error("Cannot retain the native completion receipt");
    completionFile.write(CAVI_RESPONSE); completionFile.close();
    if (!completionFile.rename(new File(CAVI_REQUEST.completion_path).name))
        throw Error("Cannot publish the native completion receipt");
}
CAVI_RESPONSE;
