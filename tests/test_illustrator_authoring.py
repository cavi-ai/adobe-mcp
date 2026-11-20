"""Illustrator SVG source/export and catalog contracts; these are not native app proof."""

import json
import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from owned_copy_fixtures import SVG, IllustratorContractBridge, png_bytes

from cavi_adobe_mcp.core.extendscript import AdobeOutcomeUnknown
from cavi_adobe_mcp.illustrator.bridge import IllustratorAuthoring, _svg

ILLUSTRATOR_TOOLS = {
    'illustrator_status', 'illustrator_inspect', 'illustrator_fonts', 'illustrator_open_copy',
    'illustrator_edit_layers', 'illustrator_add_layer', 'illustrator_place_image',
    'illustrator_add_rectangle', 'illustrator_add_path', 'illustrator_add_text',
    'illustrator_apply_mask', 'illustrator_export', 'illustrator_close_copy',
    'illustrator_operation_control', 'illustrator_set_text_font',
}


class IllustratorAuthoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bridge = IllustratorContractBridge(self.root)
        (self.root / 'source.svg').write_text(SVG)
        (self.root / 'source.png').write_bytes(png_bytes())

    def session(self):
        return self.bridge.open_copy('source.svg', 'working.ai')['session_id']

    def test_illustrator_host_has_no_photoshop_operations(self):
        for name in ('create_canvas', 'resize_image', 'generative_fill', 'generative_upscale',
                     'remove_background', '_remove_canvas_reservation'):
            with self.subTest(name=name):
                self.assertFalse(hasattr(IllustratorAuthoring, name))
        self.assertEqual(IllustratorAuthoring.INITIAL_ACTIONS, ('open_copy',))

    def test_illustrator_open_copy_accepts_svg(self):
        result = self.bridge.open_copy('source.svg', 'working.ai')
        request = self.bridge.requests[0]
        self.assertEqual((request['action'], request['app']), ('open_copy', 'illustrator'))
        self.assertEqual(Path(request['input_copy']).suffix, '.svg')
        self.assertEqual(result['copy']['relative_path'], 'working.ai')
        self.assertEqual(self.bridge._session(result['session_id'])['app'], 'illustrator')

    def test_illustrator_open_copy_refuses_unsafe_svg_and_photoshop_documents(self):
        (self.root / 'unsafe.svg').write_text('<svg><script>bad</script></svg>')
        (self.root / 'source.psd').write_bytes(b'psd')
        for source in ('unsafe.svg', 'source.psd'):
            with self.subTest(source=source), self.assertRaises((ValueError, AdobeOutcomeUnknown)):
                self.bridge.open_copy(source, 'working.ai')
        with self.assertRaises(ValueError):
            self.bridge.open_copy('source.svg', 'working.psd')
        self.assertEqual(self.bridge.requests, [])

    def test_export_defaults_to_svg_with_a_receipt(self):
        session = self.session()
        result = self.bridge.export(session, 'out.svg')
        request = self.bridge.requests[-1]
        self.assertEqual((request['kind'], request['usage']), ('svg', 'color'))
        self.assertTrue(result['artifact']['self_contained'])
        receipt = json.loads((self.root / 'out.svg.receipt.json').read_text())
        self.assertEqual(receipt['visual_acceptance'], 'unreviewed')
        self.assertEqual(receipt['generative_operations'], [])

    def test_export_png_is_explicit_and_svg_requires_color(self):
        session = self.session()
        result = self.bridge.export(session, 'out.png', kind='png', usage='data')
        self.assertEqual(self.bridge.requests[-1]['kind'], 'png')
        self.assertFalse(result['artifact']['srgb'])
        count = len(self.bridge.requests)
        for destination, kind, usage in (('out.svg', 'svg', 'data'), ('out.png', 'png', 'normal'),
                                         ('out.jpeg', 'jpeg', 'color'), ('mismatch.png', 'svg', 'color')):
            with self.subTest(destination=destination, kind=kind, usage=usage), self.assertRaises(ValueError):
                self.bridge.export(session, destination, kind=kind, usage=usage)
        self.assertEqual(len(self.bridge.requests), count)

    def test_layers_are_added_through_the_shared_group_operation(self):
        session = self.session()
        self.bridge.add_group(session, 'Sub', parent_path=[0], expected_parent_name='Top')
        request = self.bridge.requests[-1]
        self.assertEqual((request['action'], request['group_path'], request['expected_group_name']),
                         ('add_group', [0], 'Top'))

    def test_svg_rejects_external_dependencies_and_active_content(self):
        path = self.root / 'checked.svg'
        for content in ('<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///tmp/private.png"/></svg>',
                        '<svg><script>bad</script></svg>', '<svg onload="bad"/>',
                        '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///tmp/private">]><svg/>',
                        '<svg><path fill="url(https://example.com/a)"/></svg>'):
            path.write_text(content)
            with self.subTest(content=content), self.assertRaises(AdobeOutcomeUnknown):
                _svg(path)
        path.write_text('<svg><path fill="url(\'#local\')"/><image href="data:image/png;base64,AA=="/></svg>')
        self.assertTrue(_svg(path)['self_contained'])

    def test_native_svg_export_removes_external_dtd_without_fetching(self):
        path = self.root / 'export.svg'
        path.write_text('<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
                        '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd"><svg/>')
        with self.assertRaises(AdobeOutcomeUnknown):
            _svg(path)
        self.assertTrue(_svg(path, native_export=True)['self_contained'])
        self.assertNotIn(b'<!DOCTYPE', path.read_bytes())


class IllustratorProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_illustrator_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder) / 'workspace'
            workspace.mkdir()
            parameters = StdioServerParameters(command=sys.executable,
                args=['-m', 'cavi_adobe_mcp.illustrator.server'],
                env={**os.environ, 'CAVI_ADOBE_WORKSPACE': str(workspace)})
            async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as client:
                await client.initialize()
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                self.assertEqual(set(tools), ILLUSTRATOR_TOOLS)
                self.assertFalse((workspace / '.adobe-authoring').exists())
                for name, tool in tools.items():
                    with self.subTest(tool=name):
                        self.assertNotIn('application', tool.input_schema.get('properties', {}))
                        self.assertNotIn('application', tool.input_schema.get('required', []))
                export = tools['illustrator_export'].input_schema
                self.assertEqual(export['properties']['kind']['default'], 'svg')
                self.assertEqual(set(export['properties']['kind']['enum']), {'png', 'svg'})
                self.assertNotIn('kind', export['required'])
                for name in ('illustrator_status', 'illustrator_inspect', 'illustrator_fonts'):
                    self.assertTrue(tools[name].annotations.read_only_hint, name)
                for name in ('illustrator_edit_layers', 'illustrator_add_layer',
                             'illustrator_add_rectangle', 'illustrator_close_copy'):
                    self.assertTrue(tools[name].annotations.destructive_hint, name)
                for name, tool in tools.items():
                    self.assertFalse(tool.annotations.open_world_hint, name)
                rejected = await client.call_tool('illustrator_export', {
                    'session_id': '0' * 32, 'destination': 'out.svg', 'kind': 'jpeg'})
                self.assertTrue(rejected.is_error)
                self.assertEqual(rejected.structured_content['error']['outcome'], 'rejected')
                self.assertFalse((workspace / '.adobe-authoring').exists())


@unittest.skipUnless(sys.platform == 'darwin', 'native Adobe preconditions are checked on macOS')
class IllustratorPreconditionProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_not_running_application_is_a_rejected_outcome_with_its_message(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            workspace, app = root / 'workspace', root / 'Fixture.app'
            workspace.mkdir()
            (app / 'Contents' / 'MacOS').mkdir(parents=True)
            (app / 'Contents' / 'MacOS' / 'Fixture').write_bytes(b'')
            (app / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': 'com.adobe.illustrator', 'CFBundleExecutable': 'Fixture',
                'CFBundleShortVersionString': '1.0'}))
            (workspace / 'source.svg').write_text(SVG)
            parameters = StdioServerParameters(command=sys.executable,
                args=['-m', 'cavi_adobe_mcp.illustrator.server'],
                env={**os.environ, 'CAVI_ADOBE_WORKSPACE': str(workspace),
                     'CAVI_ILLUSTRATOR_APP': str(app)})
            async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as client:
                await client.initialize()
                for name, arguments in (
                        ('illustrator_open_copy', {'source': 'source.svg', 'destination': 'copy.ai'}),
                        ('illustrator_inspect', {})):
                    with self.subTest(tool=name):
                        result = await client.call_tool(name, arguments)
                        self.assertTrue(result.is_error)
                        self.assertEqual(result.structured_content['error']['outcome'], 'rejected')
                        self.assertIn('App is not running', result.content[0].text)
                self.assertFalse((workspace / 'copy.ai').exists())


if __name__ == '__main__':
    unittest.main()
