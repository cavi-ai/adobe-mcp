/* Fixed Illustrator operations for the shared prelude. No caller-supplied script is evaluated. */
function caviCollectText(parent, text) {
    for (var t = 0; t < parent.pageItems.length; t++) {
        var page = parent.pageItems[t];
        if (page.parent !== parent) continue;
        if (page.typename === "TextFrame") text.push({contents: page.contents,
            font: page.textRange.characterAttributes.textFont.name,
            size: page.textRange.characterAttributes.size});
        else if (page.typename === "GroupItem") caviCollectText(page, text);
    }
}
function caviFindFont(name) {
    var fontList = app.textFonts;
    for (var i = 0; i < fontList.length; i++)
        if (fontList[i].name === name) return fontList[i];
    return null;
}
function caviExportDocument(c, output) {
    var r = c.r, d = c.d;
    c.rgbDocument(d);
    if (d.artboards.length !== 1) throw Error("Exports require exactly one artboard");
    if (r.usage === "color" && d.colorProfileName !== "sRGB IEC61966-2.1")
        throw Error("Illustrator export requires an sRGB document; conversion is not implicit");
    var profile = r.usage === "color" ? d.colorProfileName : null;
    c.changed = true;
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
    return profile;
}
function caviRgb(rgb) {
    var color = new RGBColor(); color.red = rgb[0]; color.green = rgb[1]; color.blue = rgb[2];
    return color;
}

var CAVI_HOST = {
    capture: function () { return {dialogs: app.userInteractionLevel}; },
    enter: function () { app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS; },
    leave: function (saved) { app.userInteractionLevel = saved.dialogs; },
    layerId: function (layer) { return null; },
    locked: function (layer) { return layer.locked; },
    isContainer: function (layer) { return true; },
    layerFields: function (layer, item) {
        item.text = [];
        caviCollectText(layer, item.text);
        item.clipping_groups = 0;
        for (var g = 0; g < layer.groupItems.length; g++) if (layer.groupItems[g].clipped) item.clipping_groups++;
    },
    subpaths: function (pathItem) { return [pathItem]; },
    activate: function (d) {},
    reactivate: function (before) {},
    identity: function (d) {
        return {native_id: null, identity: "retained_document_reference_and_path"};
    },
    size: function (d) { return {width: d.width, height: d.height, units: "points"}; },
    color: function (d) { return {color_space: String(d.documentColorSpace), bit_depth: null}; },
    requireContainer: function (node, d) {},
    requireGroup: function (group) {},
    isRgb: function (d) { return d.documentColorSpace === DocumentColorSpace.RGB; },
    // The #targetengine keeps document references in $.global across commands.
    rebind: function (c) { return null; },
    fonts: function () {
        var fonts = [], collection = app.textFonts;
        for (var i = 0; i < collection.length; i++) {
            var font = collection[i];
            fonts.push({name: font.name, family: font.family, style: font.style,
                        postscript_name: font.name});
        }
        return fonts;
    },
    saveCopy: function (d, target) {
        var ai = new IllustratorSaveOptions(); ai.pdfCompatible = true; ai.embedICCProfile = true;
        d.saveAs(target, ai);
    },
    exportDocument: caviExportDocument,
    initial: {},
    direct: {},
    ops: {
        edit_layers: {
            check: function (c) {
                c.targets = [];
                for (var i = 0; i < c.r.edits.length; i++) {
                    var item = c.layerAt(c.d, c.r.edits[i].path, c.r.edits[i].expected_name);
                    if (item.locked) throw Error("A target layer is locked");
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
                if (b[0] + b[2] > c.d.width || b[1] + b[3] > c.d.height)
                    throw Error("Rectangle exceeds document bounds");
            },
            run: function (c) {
                var r = c.r, d = c.d, b = r.bounds;
                var board = d.artboards[0].artboardRect;
                var layer = d.layers.add(); layer.name = r.name;
                var item = layer.pathItems.rectangle(board[1] - b[1], board[0] + b[0], b[2], b[3]);
                item.fillColor = caviRgb(r.rgb); item.stroked = false;
                layer.opacity = r.opacity;
                c.item = layer;
            }
        },
        add_group: {
            check: function (c) { c.group = c.groupAt(c.d); },
            run: function (c) { c.item = c.group.layers.add(); c.item.name = c.r.name; }
        },
        place_image: {
            check: function (c) {
                c.group = c.groupAt(c.d);
                c.rgbDocument(c.d);
                if (!(new File(c.r.source)).exists) throw Error("Staged PNG does not exist");
            },
            run: function (c) {
                var r = c.r, d = c.d;
                var placed = d.placedItems.add(); placed.file = new File(r.source);
                var artboard = d.artboards[0].artboardRect;
                placed.position = [artboard[0] + r.position[0], artboard[1] - r.position[1]];
                var targetLayer = c.group === d ? d.layers.add() : c.group.layers.add();
                targetLayer.name = r.name;
                placed.move(targetLayer, ElementPlacement.PLACEATBEGINNING);
                placed.embed(); c.item = targetLayer;
            }
        },
        add_path: {
            check: function (c) {
                c.rgbDocument(c.d);
                c.group = c.groupAt(c.d);
            },
            run: function (c) {
                var r = c.r, d = c.d;
                var item = c.group.layers.add(); item.name = r.name;
                var authoredPath = item.pathItems.add(); authoredPath.name = r.name;
                var boardPath = d.artboards[0].artboardRect;
                function mapped(pair) { return [boardPath[0] + pair[0], boardPath[1] - pair[1]]; }
                for (var i = 0; i < r.points.length; i++) {
                    var aiPoint = authoredPath.pathPoints.add();
                    aiPoint.anchor = mapped(r.points[i].anchor); aiPoint.leftDirection = mapped(r.points[i].left);
                    aiPoint.rightDirection = mapped(r.points[i].right); aiPoint.pointType = PointType.CORNER;
                }
                authoredPath.closed = r.closed; authoredPath.stroked = false; authoredPath.filled = r.rgb !== null;
                if (r.rgb !== null) authoredPath.fillColor = caviRgb(r.rgb);
                c.item = item;
            }
        },
        add_text: {
            check: function (c) {
                c.rgbDocument(c.d);
                var chosen = caviFindFont(c.r.font);
                if (!chosen) throw Error("Exact PostScript font is unavailable; use fonts discovery");
                c.font = chosen;
            },
            run: function (c) {
                var r = c.r, d = c.d;
                var item = d.layers.add(); item.name = r.name;
                var textFrame = item.textFrames.add(); textFrame.contents = r.text;
                var tb = d.artboards[0].artboardRect;
                textFrame.position = [tb[0] + r.position[0], tb[1] - r.position[1]];
                textFrame.textRange.characterAttributes.textFont = c.font;
                textFrame.textRange.characterAttributes.size = r.size;
                textFrame.textRange.characterAttributes.fillColor = caviRgb(r.rgb);
                if (textFrame.textRange.characterAttributes.textFont.name !== r.font)
                    throw Error("Native text substituted the requested font: "
                                + textFrame.textRange.characterAttributes.textFont.name);
                c.item = item;
            }
        },
        set_text_font: {
            check: function (c) {
                var r = c.r, item = c.layerAt(c.d, r.layer_path, r.expected_name);
                if (item.locked) throw Error("Target layer is locked");
                if (r.frame_index >= item.textFrames.length) throw Error("Text frame no longer exists");
                c.frame = item.textFrames[r.frame_index];
                if (c.frame.locked) throw Error("Target text frame is locked");
                c.font = caviFindFont(r.font);
                if (!c.font) throw Error("Exact PostScript font is unavailable; use fonts discovery");
                c.item = item;
            },
            run: function (c) {
                var attributes = c.frame.textRange.characterAttributes;
                attributes.textFont = c.font;
                if (attributes.textFont.name !== c.r.font)
                    throw Error("Native text substituted the requested font: " + attributes.textFont.name);
            }
        },
        apply_mask: {
            check: function (c) {
                var r = c.r, d = c.d;
                var item = c.layerAt(d, r.layer_path, r.expected_name);
                if (item.locked) throw Error("Mask target is locked");
                var maskPath = null, matches = 0;
                for (var i = 0; i < d.pathItems.length; i++)
                    if (d.pathItems[i].name === r.path_name) { maskPath = d.pathItems[i]; matches++; }
                if (matches !== 1 || !maskPath.closed) throw Error("Mask requires one exact closed path");
                if (item.pageItems.length === 0) throw Error("Clip layer contains no artwork");
                c.item = item; c.maskPath = maskPath;
            },
            run: function (c) {
                var item = c.item;
                var clipGroup = item.groupItems.add(), direct = [];
                for (var i = 0; i < item.pageItems.length; i++)
                    if (item.pageItems[i] !== clipGroup && item.pageItems[i].parent === item) direct.push(item.pageItems[i]);
                for (i = 0; i < direct.length; i++) direct[i].move(clipGroup, ElementPlacement.PLACEATEND);
                var clip = c.maskPath.duplicate(clipGroup, ElementPlacement.PLACEATBEGINNING);
                clip.clipping = true; clipGroup.clipped = true;
            }
        }
    }
};
var CAVI_RESPONSE = caviRun(CAVI_REQUEST, CAVI_HOST);
caviPublish(CAVI_REQUEST, CAVI_RESPONSE);
CAVI_RESPONSE;
