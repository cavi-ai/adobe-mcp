/* Fixed Photoshop operations for the shared prelude. No caller-supplied script is evaluated. */
function caviUserMask(layer) {
    var ref = new ActionReference(); ref.putIdentifier(charIDToTypeID("Lyr "), layer.id);
    var desc = executeActionGet(ref);
    return desc.hasKey(stringIDToTypeID("hasUserMask")) && desc.getBoolean(stringIDToTypeID("hasUserMask"));
}
function caviWholeNumber(value) {
    return typeof value === "number" && isFinite(value) && Math.floor(value) === value;
}
function caviCreateCanvas(c) {
    var r = c.r, registry = c.registry, i;
    if (r.expected_document !== null && r.expected_document !== undefined)
        throw Error("Canvas creation requires a new session without an existing native document");
    if (registry[r.session_id]) throw Error("Session already has native ownership");
    if (!caviWholeNumber(r.canvas_width) || r.canvas_width < 1 || r.canvas_width > 2880
            || !caviWholeNumber(r.canvas_height) || r.canvas_height < 1 || r.canvas_height > 2880)
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
        if (caviPath(app.documents[i]) === destination.fsName) throw Error("Canvas destination is already open");

    c.changed = true;
    var d = app.documents.add(new UnitValue(r.canvas_width, "px"), new UnitValue(r.canvas_height, "px"),
                              72, destination.name.replace(/\.psd$/i, ""), NewDocumentMode.RGB,
                              DocumentFill.TRANSPARENT, 1, canvasDepth,
                              "sRGB IEC61966-2.1");
    if (!d || !caviOpen(d) || d.width.as("px") !== r.canvas_width || d.height.as("px") !== r.canvas_height
            || d.resolution !== 72 || d.mode !== DocumentMode.RGB
            || d.bitsPerChannel !== canvasDepth
            || d.colorProfileName !== "sRGB IEC61966-2.1"
            || d.layers.length !== 1 || d.layers[0].isBackgroundLayer !== false)
        throw Error("Photoshop did not create the requested transparent RGB sRGB canvas");
    app.activeDocument = d;
    var canvasOptions = new PhotoshopSaveOptions();
    canvasOptions.layers = true; canvasOptions.embedColorProfile = true;
    d.saveAs(destination, canvasOptions, false, Extension.LOWERCASE);
    if (caviPath(d) !== r.document_path || !destination.exists || destination.length <= 0)
        throw Error("Saved canvas does not match its reserved PSD destination");
    var result = c.snapshot(d);
    result.resolution = d.resolution;
    result.canvas_background = "transparent";
    result.has_background_layer = d.layers[0].isBackgroundLayer;
    registry[r.session_id] = {token: r.ownership_token, path: r.document_path,
                             document: d, observed: caviJson(c.snapshot(d))};
    return result;
}
function caviResizeImage(c) {
    var r = c.r, d = c.d;
    if (!caviWholeNumber(r.resize_width) || r.resize_width < 1 || r.resize_width > 2880
            || !caviWholeNumber(r.resize_height) || r.resize_height < 1 || r.resize_height > 2880)
        throw Error("Image width and height must be integers in 1..2880");
    c.changed = true;
    d.resizeImage(new UnitValue(r.resize_width, "px"), new UnitValue(r.resize_height, "px"),
                  d.resolution, ResampleMethod.BICUBICSHARPER);
    if (d.width.as("px") !== r.resize_width || d.height.as("px") !== r.resize_height)
        throw Error("Photoshop did not apply the requested image dimensions");
    c.saveOwned(d);
    var result = c.snapshot(d);
    if (result.width !== r.resize_width || result.height !== r.resize_height)
        throw Error("Photoshop resize readback differs from the requested dimensions");
    return result;
}
function caviTextRuns(layer, budget) {
    // The style runs of one text layer: range, PostScript font and whether Photoshop can use it.
    var ref = new ActionReference(); ref.putIdentifier(charIDToTypeID("Lyr "), layer.id);
    var ranges = executeActionGet(ref).getObjectValue(stringIDToTypeID("textKey"))
        .getList(stringIDToTypeID("textStyleRange"));
    var psKey = stringIDToTypeID("fontPostScriptName"), availableKey = stringIDToTypeID("fontAvailable");
    var runs = [];
    for (var i = 0; i < ranges.count; i++) {
        if (--budget.remaining < 0) throw Error("Document exceeds the 4096-run text font limit");
        var range = ranges.getObjectValue(i), style = range.getObjectValue(stringIDToTypeID("textStyle"));
        runs.push({from: range.getInteger(stringIDToTypeID("from")), to: range.getInteger(stringIDToTypeID("to")),
                   postscript_name: style.hasKey(psKey) ? style.getString(psKey) : null,
                   available: style.hasKey(availableKey) ? style.getBoolean(availableKey) : false});
    }
    return runs;
}
function caviRequireFont(name) {
    var fontList = app.fonts;
    for (var i = 0; i < fontList.length; i++)
        if (fontList[i].postScriptName === name) return;
    throw Error("Exact PostScript font is unavailable; use fonts discovery");
}
function caviRequireRunFont(layer, font) {
    // Assigning a font Photoshop cannot use raises nothing; the runs show what the layer really uses.
    var runs = caviTextRuns(layer, {remaining: 4096});
    if (!runs.length) throw Error("Native text reports no style runs");
    for (var i = 0; i < runs.length; i++)
        if (runs[i].postscript_name !== font || runs[i].available !== true)
            throw Error("Native text run uses another or unavailable font: " + runs[i].postscript_name);
}
function caviTextFonts(c) {
    var items = c.layers(c.d, [], {remaining: 2048}), layers = [], budget = {remaining: 4096};
    for (var i = 0; i < items.length; i++) {
        if (items[i].text === undefined) continue;
        var layer = c.layerAt(c.d, items[i].path, items[i].name);
        layers.push({path: items[i].path, name: items[i].name, native_id: items[i].id,
                     runs: caviTextRuns(layer, budget)});
    }
    return {layers: layers};
}
function caviExportDocument(c, output) {
    var r = c.r, d = c.d, profile = null;
    if (r.kind !== "png") throw Error("Photoshop SVG export is unsupported");
    if (d.bitsPerChannel !== BitsPerChannelType.EIGHT && d.bitsPerChannel !== BitsPerChannelType.SIXTEEN)
        throw Error("PNG export preserves 8/16-bit documents; convert 32-bit sources explicitly");
    if (r.usage === "color") {
        c.rgbDocument(d);
        if (d.colorProfileType === ColorProfile.NONE)
            throw Error("Color PNG requires a tagged RGB source document");
    }
    else if (d.mode !== DocumentMode.RGB && d.mode !== DocumentMode.GRAYSCALE)
        throw Error("Data PNG requires RGB or grayscale; conversion is not implicit");
    c.changed = true;
    c.temporary = d.duplicate("Cavi PNG export " + r.operation_id, false);
    if (r.usage === "color") {
        c.temporary.convertProfile("sRGB IEC61966-2.1", Intent.RELATIVECOLORIMETRIC, true, false);
        profile = c.temporary.colorProfileName;
    } else c.temporary.colorProfileType = ColorProfile.NONE;
    var png = new PNGSaveOptions(); png.interlaced = false; png.compression = 6;
    c.temporary.saveAs(output, png, true, Extension.LOWERCASE);
    c.temporary.close(SaveOptions.DONOTSAVECHANGES); c.temporary = null;
    return profile;
}
function caviPoints(r) {
    var pts = [];
    for (var i = 0; i < r.points.length; i++) {
        var pp = new PathPointInfo(); pp.kind = PointKind.CORNERPOINT;
        pp.anchor = r.points[i].anchor; pp.leftDirection = r.points[i].left;
        pp.rightDirection = r.points[i].right; pts.push(pp);
    }
    return pts;
}
function caviSolid(rgb) {
    var color = new SolidColor(); color.rgb.red = rgb[0];
    color.rgb.green = rgb[1]; color.rgb.blue = rgb[2];
    return color;
}
function caviSelectBounds(d, b) {
    d.selection.select([[b[0], b[1]], [b[0] + b[2], b[1]],
                        [b[0] + b[2], b[1] + b[3]], [b[0], b[1] + b[3]]]);
}

var CAVI_HOST = {
    capture: function () { return {dialogs: app.displayDialogs, units: app.preferences.rulerUnits}; },
    enter: function () { app.displayDialogs = DialogModes.NO; app.preferences.rulerUnits = Units.PIXELS; },
    leave: function (saved) { app.displayDialogs = saved.dialogs; app.preferences.rulerUnits = saved.units; },
    layerId: function (layer) { return layer.id; },
    locked: function (layer) { return layer.allLocked; },
    isContainer: function (layer) { return layer.typename === "LayerSet"; },
    layerFields: function (layer, item) {
        if (layer.typename === "ArtLayer" && layer.kind === LayerKind.TEXT) {
            item.text = {contents: layer.textItem.contents, font: layer.textItem.font,
                         size: layer.textItem.size.as("pt")};
        }
        item.has_user_mask = caviUserMask(layer);
    },
    subpaths: function (pathItem) { return pathItem.subPathItems; },
    activate: function (d) { app.activeDocument = d; },
    reactivate: function (before) { if (before && caviOpen(before)) app.activeDocument = before; },
    identity: function (d) {
        return {native_id: d.id, identity: "document_id_and_retained_reference"};
    },
    size: function (d) { return {width: d.width.as("px"), height: d.height.as("px"), units: "pixels"}; },
    color: function (d) {
        return {color_space: String(d.mode), bit_depth: String(d.bitsPerChannel)};
    },
    requireContainer: function (node, d) {
        if (node !== d && node.typename !== "LayerSet") throw Error("Layer path is not a group");
    },
    requireGroup: function (group) {
        if (group.typename !== "LayerSet") throw Error("Target is not a layer group");
    },
    isRgb: function (d) { return d.mode === DocumentMode.RGB; },
    // Photoshop ignores #targetengine, so AppleEvent scripts cannot keep a DOM reference
    // in $.global. Rebind only the exact same live document id/path and require the last
    // server-journaled snapshot to match before permitting any owned-copy operation.
    rebind: function (c) {
        var r = c.r;
        if (!r.expected_document || r.expected_document.native_id === null) return null;
        for (var n = 0; n < app.documents.length; n++) {
            var candidate = app.documents[n];
            if (candidate.id === r.expected_document.native_id
                    && caviPath(candidate) === r.document_path) {
                var entry = {token: r.ownership_token, path: r.document_path,
                             document: candidate, observed: caviJson(r.expected_document)};
                c.registry[r.session_id] = entry;
                return entry;
            }
        }
        return null;
    },
    fonts: function () {
        var fonts = [], collection = app.fonts;
        for (var i = 0; i < collection.length; i++) {
            var font = collection[i];
            fonts.push({name: font.name, family: font.family, style: font.style,
                        postscript_name: font.postScriptName});
        }
        return fonts;
    },
    saveCopy: function (d, target) {
        var psd = new PhotoshopSaveOptions(); psd.layers = true; psd.embedColorProfile = true;
        d.saveAs(target, psd, false, Extension.LOWERCASE);
    },
    exportDocument: caviExportDocument,
    initial: {create_canvas: caviCreateCanvas},
    direct: {resize_image: caviResizeImage, text_fonts: caviTextFonts},
    ops: {
        edit_layers: {
            check: function (c) {
                c.targets = [];
                for (var i = 0; i < c.r.edits.length; i++) {
                    var item = c.layerAt(c.d, c.r.edits[i].path, c.r.edits[i].expected_name);
                    if (item.allLocked) throw Error("A target layer is locked");
                    c.targets.push(item);
                }
            },
            run: function (c) {
                for (var i = 0; i < c.targets.length; i++) {
                    var edit = c.r.edits[i], item = c.targets[i];
                    if (edit.name !== undefined) item.name = edit.name;
                    if (edit.visible !== undefined) item.visible = edit.visible;
                    if (edit.opacity !== undefined) item.opacity = edit.opacity;
                }
            }
        },
        add_rectangle: {
            check: function (c) {
                c.rgbDocument(c.d);
                var b = c.r.bounds;
                if (b[0] + b[2] > c.d.width.as("px") || b[1] + b[3] > c.d.height.as("px"))
                    throw Error("Rectangle exceeds document bounds");
            },
            run: function (c) {
                var r = c.r, d = c.d, b = r.bounds;
                var item = d.artLayers.add(); item.name = r.name;
                caviSelectBounds(d, b);
                d.selection.fill(caviSolid(r.rgb), ColorBlendMode.NORMAL, 100, false); d.selection.deselect();
                item.opacity = r.opacity;
                c.item = item;
            }
        },
        add_group: {
            check: function (c) { c.group = c.groupAt(c.d); },
            run: function (c) { c.item = c.group.layerSets.add(); c.item.name = c.r.name; }
        },
        place_image: {
            check: function (c) {
                c.group = c.groupAt(c.d);
                c.rgbDocument(c.d);
                if (!(new File(c.r.source)).exists) throw Error("Staged PNG does not exist");
            },
            run: function (c) {
                var r = c.r, d = c.d;
                c.temporary = app.open(new File(r.source));
                var item = c.temporary.activeLayer.duplicate(d, ElementPlacement.PLACEATBEGINNING);
                c.temporary.close(SaveOptions.DONOTSAVECHANGES); c.temporary = null;
                app.activeDocument = d; item.name = r.name;
                if (c.group !== d) item.move(c.group, ElementPlacement.INSIDE);
                item.translate(r.position[0] - item.bounds[0].as("px"),
                               r.position[1] - item.bounds[1].as("px"));
                c.item = item;
            }
        },
        add_path: {
            check: function (c) {
                c.rgbDocument(c.d);
                c.group = c.groupAt(c.d);
                for (var i = 0; i < c.d.pathItems.length; i++)
                    if (c.d.pathItems[i].name === c.r.name) throw Error("Path name already exists");
            },
            run: function (c) {
                var r = c.r, d = c.d;
                var sub = new SubPathInfo();
                sub.closed = r.closed; sub.operation = ShapeOperation.SHAPEADD;
                sub.entireSubPath = caviPoints(r);
                var authoredPath = d.pathItems.add(r.name, [sub]);
                if (r.rgb !== null) {
                    c.item = c.group.artLayers.add(); c.item.name = r.name;
                    authoredPath.fillPath(caviSolid(r.rgb), ColorBlendMode.NORMAL, 100, false, 0, true);
                }
            }
        },
        add_text: {
            check: function (c) {
                c.rgbDocument(c.d);
                caviRequireFont(c.r.font);
            },
            run: function (c) {
                var r = c.r;
                var item = c.d.artLayers.add(); item.kind = LayerKind.TEXT; item.name = r.name;
                var textItem = item.textItem; textItem.contents = r.text; textItem.font = r.font;
                textItem.position = r.position; textItem.size = new UnitValue(r.size, "pt");
                textItem.color = caviSolid(r.rgb);
                if (textItem.font !== r.font) throw Error("Native text substituted the requested font: " + textItem.font);
                caviRequireRunFont(item, r.font);
                c.item = item;
            }
        },
        set_text_font: {
            check: function (c) {
                var r = c.r, item = c.layerAt(c.d, r.layer_path, r.expected_name);
                if (item.typename !== "ArtLayer" || item.kind !== LayerKind.TEXT)
                    throw Error("Target is not a text layer");
                if (item.allLocked) throw Error("Target layer is locked");
                caviRequireFont(r.font);
                c.item = item;
            },
            run: function (c) {
                c.item.textItem.font = c.r.font;
                caviRequireRunFont(c.item, c.r.font);
            }
        },
        apply_mask: {
            check: function (c) {
                var r = c.r, d = c.d;
                var item = c.layerAt(d, r.layer_path, r.expected_name);
                if (item.allLocked) throw Error("Mask target is locked");
                var maskPath = null, matches = 0;
                for (var i = 0; i < d.pathItems.length; i++)
                    if (d.pathItems[i].name === r.path_name) { maskPath = d.pathItems[i]; matches++; }
                if (matches !== 1 || maskPath.subPathItems.length !== 1 || !maskPath.subPathItems[0].closed)
                    throw Error("Mask requires one exact closed path");
                app.activeDocument = d; d.activeLayer = item;
                if (caviUserMask(item)) throw Error("Layer already has a mask");
                c.item = item; c.maskPath = maskPath;
            },
            run: function (c) {
                c.d.activeLayer = c.item;
                c.maskPath.makeSelection(0, true, SelectionType.REPLACE);
                var md = new ActionDescriptor(), mr = new ActionReference();
                md.putClass(charIDToTypeID("Nw  "), charIDToTypeID("Chnl"));
                mr.putEnumerated(charIDToTypeID("Chnl"), charIDToTypeID("Chnl"), charIDToTypeID("Msk "));
                md.putReference(charIDToTypeID("At  "), mr);
                md.putEnumerated(charIDToTypeID("Usng"), charIDToTypeID("UsrM"), charIDToTypeID("RvlS"));
                executeAction(charIDToTypeID("Mk  "), md, DialogModes.NO); c.d.selection.deselect();
            }
        },
        generative_fill: {
            check: function (c) {
                c.rgbDocument(c.d);
                var r = c.r;
                if (typeof r.prompt !== "string" || r.prompt.length > 1000)
                    throw Error("Prompt must be at most 1000 characters");
                if (r.bounds && (r.bounds[0] + r.bounds[2] > c.d.width.as("px")
                        || r.bounds[1] + r.bounds[3] > c.d.height.as("px")))
                    throw Error("Fill bounds exceed document bounds");
            },
            run: function (c) {
                var r = c.r, d = c.d;
                var layerTotal = c.layers(d, [], {remaining: 2048}).length, fb = r.bounds;
                if (fb) caviSelectBounds(d, fb);
                else d.selection.selectAll();
                var fd = new ActionDescriptor(), fr = new ActionReference();
                fr.putEnumerated(stringIDToTypeID("document"), stringIDToTypeID("ordinal"), stringIDToTypeID("targetEnum"));
                fd.putReference(stringIDToTypeID("null"), fr);
                fd.putString(stringIDToTypeID("prompt"), r.prompt);
                fd.putString(stringIDToTypeID("serviceID"), "clio");
                fd.putEnumerated(stringIDToTypeID("mode"), stringIDToTypeID("syntheticFillMode"), stringIDToTypeID("inpaint"));
                executeAction(stringIDToTypeID("syntheticFill"), fd, DialogModes.NO);
                d.selection.deselect();
                if (c.layers(d, [], {remaining: 2048}).length !== layerTotal + 1)
                    throw Error("Photoshop did not add a generated layer");
                c.item = d.activeLayer;
            }
        },
        remove_background: {
            check: function (c) {
                c.rgbDocument(c.d);
                var item = c.layerAt(c.d, c.r.layer_path, c.r.expected_name);
                if (item.typename !== "ArtLayer" || item.isBackgroundLayer
                        || (item.kind !== LayerKind.NORMAL && item.kind !== LayerKind.SMARTOBJECT))
                    throw Error("Remove Background requires a pixel or smart object layer that is not the background");
                if (item.allLocked) throw Error("Target layer is locked");
                if (caviUserMask(item)) throw Error("Layer already has a mask");
                c.item = item;
            },
            run: function (c) {
                c.d.activeLayer = c.item;
                executeAction(stringIDToTypeID("removeBackground"), new ActionDescriptor(), DialogModes.NO);
                if (!caviUserMask(c.item)) throw Error("Photoshop did not add a subject mask");
            }
        },
        generative_upscale: {
            check: function (c) {
                c.rgbDocument(c.d);
                c.docWidth = c.d.width.as("px"); c.docHeight = c.d.height.as("px");
                if (c.docWidth > 1440 || c.docHeight > 1440)
                    throw Error("Generative Upscale requires at most 1440 px per side");
            },
            run: function (c) {
                var d = c.d, docWidth = c.docWidth, docHeight = c.docHeight;
                var openBefore = app.documents.length;
                executeAction(stringIDToTypeID("generativeUpscale"), new ActionDescriptor(), DialogModes.NO);
                if (app.documents.length !== openBefore + 1 || app.activeDocument == d)
                    throw Error("Photoshop did not open an upscaled document");
                c.temporary = app.activeDocument;
                if (c.temporary.width.as("px") !== docWidth * 2 || c.temporary.height.as("px") !== docHeight * 2)
                    throw Error("Upscaled document is not twice the source size");
                if (c.temporary.layers.length > 1) c.temporary.mergeVisibleLayers();
                app.activeDocument = d;
                d.resizeImage(new UnitValue(docWidth * 2, "px"), new UnitValue(docHeight * 2, "px"),
                              d.resolution, ResampleMethod.BICUBIC);
                app.activeDocument = c.temporary;
                var item = c.temporary.layers[0].duplicate(d, ElementPlacement.PLACEATBEGINNING);
                c.temporary.close(SaveOptions.DONOTSAVECHANGES); c.temporary = null;
                app.activeDocument = d; item.name = "Generative Upscale";
                c.item = item;
            }
        }
    }
};
var CAVI_RESPONSE = caviRun(CAVI_REQUEST, CAVI_HOST);
caviPublish(CAVI_REQUEST, CAVI_RESPONSE);
CAVI_RESPONSE;
