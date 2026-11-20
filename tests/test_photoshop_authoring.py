"""Photoshop canvas, resize, generative and export contracts; these are not native app proof."""

import json
import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from owned_copy_fixtures import ContractBridge, png_bytes

from cavi_adobe_mcp.core.extendscript import AdobeCommandRejected, AdobeOutcomeUnknown
from cavi_adobe_mcp.photoshop.bridge import digest

PHOTOSHOP_TOOLS = {
    'photoshop_status', 'photoshop_inspect', 'photoshop_fonts', 'photoshop_open_copy',
    'photoshop_create_canvas', 'photoshop_resize_image', 'photoshop_edit_layers',
    'photoshop_add_group', 'photoshop_place_image', 'photoshop_add_rectangle',
    'photoshop_add_path', 'photoshop_add_text', 'photoshop_apply_mask',
    'photoshop_generative_fill', 'photoshop_remove_background', 'photoshop_generative_upscale',
    'photoshop_export', 'photoshop_close_copy', 'photoshop_operation_control',
    'photoshop_text_fonts', 'photoshop_set_text_font',
}


class PhotoshopAuthoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bridge = ContractBridge(self.root)
        self.source = self.root / 'source.png'
        self.source.write_bytes(png_bytes())

    def session(self):
        return self.bridge.open_copy('source.png', 'working.psd')['session_id']

    def test_canvas_depth_is_explicit_and_rejects_unsupported_values(self):
        result = self.bridge.create_canvas(32, 32, 'selected.psd', bit_depth=8)
        self.assertEqual(result['document']['bit_depth'], 'BitsPerChannelType.EIGHT')
        for depth in (32, True, 8.0, None):
            with self.subTest(depth=depth), self.assertRaises(ValueError):
                self.bridge.create_canvas(32, 32, 'invalid.psd', bit_depth=depth)

    def test_resize_image_validates_before_dispatch_and_returns_document_snapshot(self):
        session = self.session()
        result = self.bridge.resize_image(session, 768, 768)
        request = self.bridge.requests[-1]
        self.assertEqual(request['action'], 'resize_image')
        self.assertEqual((request['resize_width'], request['resize_height']), (768, 768))
        self.assertEqual((result['document']['width'], result['document']['height']), (768, 768))
        self.assertIn('layers', result['document'])
        self.assertIn('paths', result['document'])
        count = len(self.bridge.requests)
        for width, height in ((0, 1), (2881, 1), (True, 2), (2.0, 2), (2, None)):
            with self.subTest(width=width, height=height), self.assertRaises(ValueError):
                self.bridge.resize_image(session, width, height)
        self.assertEqual(len(self.bridge.requests), count)

    def test_generative_fill_validates_before_dispatch_and_returns_document_snapshot(self):
        session = self.session()
        result = self.bridge.generative_fill(session, 'a red apple', [0, 0, 1, 1])
        request = self.bridge.requests[-1]
        self.assertEqual((request['action'], request['prompt'], request['bounds']),
                         ('generative_fill', 'a red apple', [0, 0, 1, 1]))
        self.assertIn('layers', result['document'])
        self.assertFalse(result['retry_safe'])
        self.bridge.generative_fill(session, '')
        self.assertIsNone(self.bridge.requests[-1]['bounds'])
        count = len(self.bridge.requests)
        for prompt, bounds in ((None, None), ('x' * 1001, None), ('a\nb', None),
                               ('ok', [0, 0, 0, 1]), ('ok', [0, 0, 1]), ('ok', [-1, 0, 1, 1])):
            with self.subTest(prompt=prompt, bounds=bounds), self.assertRaises(ValueError):
                self.bridge.generative_fill(session, prompt, bounds)
        self.assertEqual(len(self.bridge.requests), count)

    def test_remove_background_targets_one_exact_layer(self):
        session = self.session()
        result = self.bridge.remove_background(session, [0], 'Subject')
        request = self.bridge.requests[-1]
        self.assertEqual((request['action'], request['layer_path'], request['expected_name']),
                         ('remove_background', [0], 'Subject'))
        self.assertIn('layers', result['document'])
        count = len(self.bridge.requests)
        for path, name in (([], 'Subject'), ([0], ''), ([True], 'Subject'), (None, 'Subject')):
            with self.subTest(path=path, name=name), self.assertRaises(ValueError):
                self.bridge.remove_background(session, path, name)
        self.assertEqual(len(self.bridge.requests), count)

    def test_generative_upscale_dispatches_once_for_an_owned_copy(self):
        session = self.session()
        result = self.bridge.generative_upscale(session)
        self.assertEqual(self.bridge.requests[-1]['action'], 'generative_upscale')
        self.assertIn('layers', result['document'])

    def test_generated_pixels_are_recorded_and_carried_into_the_export_receipt(self):
        session = self.session()
        fill = self.bridge.generative_fill(session, 'a red apple', [0, 0, 1, 1])
        self.assertEqual(fill['generative'], {
            'tool': 'photoshop_generative_fill', 'service': 'Adobe Firefly in Photoshop',
            'prompt': 'a red apple', 'bounds': [0, 0, 1, 1],
            'layers': [{'path': [0], 'name': 'Generated generative_fill'}]})
        upscale = self.bridge.generative_upscale(session)
        self.assertEqual(upscale['generative'], {
            'tool': 'photoshop_generative_upscale', 'service': 'Adobe Firefly in Photoshop',
            'prompt': None, 'bounds': None,
            'layers': [{'path': [0], 'name': 'Generated generative_upscale'}]})
        self.bridge.remove_background(session, [0], 'Subject')
        expected = [{**fill['generative'], 'operation_id': fill['operation_id']},
                    {**upscale['generative'], 'operation_id': upscale['operation_id']}]
        saved = json.loads((self.root / '.adobe-authoring' / (session + '.json')).read_text())
        self.assertEqual(saved['generative_operations'], expected)
        result = self.bridge.export(session, 'out.png')
        receipt = json.loads((self.root / 'out.png.receipt.json').read_text())
        self.assertEqual(result['generative_operations'], expected)
        self.assertEqual(receipt['generative_operations'], expected)
        inspected = self.bridge.inspect_session(session)['session']
        self.assertNotIn('ownership_token', inspected)
        self.assertEqual(inspected['generative_operations'], expected)

    def test_failed_generative_operation_is_not_recorded(self):
        session = self.session()
        self.bridge.error = AdobeCommandRejected('rejected by native preflight')
        with self.assertRaises(AdobeCommandRejected):
            self.bridge.generative_fill(session, 'a red apple')
        self.bridge.error = None
        self.assertNotIn('generative_operations', self.bridge._session(session))

    def test_export_without_generative_operations_lists_none(self):
        session = self.session()
        self.bridge.add_rectangle(session, 'Box', [0, 0, 1, 1], [1, 2, 3])
        result = self.bridge.export(session, 'plain.png')
        receipt = json.loads((self.root / 'plain.png.receipt.json').read_text())
        self.assertEqual(result['generative_operations'], [])
        self.assertEqual(receipt['generative_operations'], [])

    def test_photoshop_export_refuses_svg(self):
        session = self.session()
        count = len(self.bridge.requests)
        with self.assertRaises(TypeError):
            self.bridge.export(session, 'out.svg', kind='svg')
        for destination, usage in (('out.svg', 'color'), ('out.png', 'normal'), ('out.jpeg', 'color')):
            with self.subTest(destination=destination, usage=usage), self.assertRaises(ValueError):
                self.bridge.export(session, destination, usage=usage)
        self.assertEqual(len(self.bridge.requests), count)

    def test_canvas_recipe_is_retained_and_native_result_is_pinned(self):
        result = self.bridge.create_canvas(320, 180, 'canvas.psd')
        request = self.bridge.requests[0]
        packet = self.bridge._session(result['session_id'])
        recipe = json.loads(Path(packet['source']).read_text())
        self.assertEqual(request['action'], 'create_canvas')
        self.assertEqual((request['canvas_width'], request['canvas_height']), (320, 180))
        self.assertEqual(request['canvas_bit_depth'], 16)
        self.assertEqual(request['input_copy'], packet['input_copy'])
        self.assertEqual(digest(Path(packet['source'])), digest(Path(packet['input_copy'])))
        self.assertEqual(result['source_kind'], 'canvas_recipe')
        self.assertEqual(result['source_sha256_kind'], 'canvas_recipe_json')
        self.assertEqual(result['source_sha256'], digest(Path(packet['source'])))
        self.assertEqual(result['canvas_recipe']['sha256'], result['source_sha256'])
        self.assertEqual(recipe, {'version': 1, 'kind': 'photoshop_canvas_recipe', 'width': 320,
                                  'height': 180, 'resolution': 72, 'mode': 'RGB', 'bit_depth': 16,
                                  'background': 'transparent',
                                  'color_profile': 'sRGB IEC61966-2.1'})
        self.assertEqual(packet['state'], 'owned')

    def test_canvas_arguments_are_bounded_before_native_dispatch(self):
        for width, height in ((0, 1), (2881, 1), (True, 1), (1, 1.5)):
            with self.subTest(width=width, height=height), self.assertRaises(ValueError):
                self.bridge.create_canvas(width, height, 'bad.psd')
        self.assertEqual(self.bridge.requests, [])

    def test_canvas_native_rejection_is_recorded_rejected_and_not_owned(self):
        self.bridge.error = AdobeCommandRejected('Photoshop preflight rejected canvas')
        with self.assertRaises(AdobeCommandRejected):
            self.bridge.create_canvas(16, 16, 'rejected.psd')
        packet_path = next((self.root / '.adobe-authoring').glob('*.json'))
        packet = json.loads(packet_path.read_text())
        self.assertEqual(packet['state'], 'rejected')
        self.assertEqual(packet['last_operation']['state'], 'rejected')
        self.assertFalse((self.root / 'pending.json').exists())
        self.assertFalse((self.root / 'rejected.psd').exists())

    def test_canvas_rejection_preserves_changed_or_replaced_destination(self):
        for name, replace in (
                ('changed.psd', lambda path: path.write_bytes(b'changed by another writer')),
                ('replaced.psd', lambda path: (path.unlink(), path.write_bytes(b'new user file')))):
            with self.subTest(name=name):
                def reject_after_external_change(request, _state, _directory, replacement=replace):
                    replacement(Path(request['document_path']))
                    raise AdobeCommandRejected('preflight rejection')
                self.bridge._native = reject_after_external_change
                with self.assertRaises(AdobeCommandRejected):
                    self.bridge.create_canvas(16, 16, name)
                self.assertEqual((self.root / name).read_bytes(), b'new user file'
                                 if name == 'replaced.psd' else b'changed by another writer')
                self.assertFalse((self.root / 'pending.json').exists())
                self.bridge._native = ContractBridge._native.__get__(self.bridge, ContractBridge)

    def test_canvas_unknown_outcome_retains_pending_and_prevents_replay(self):
        self.bridge.error = AdobeOutcomeUnknown('native canvas result unknown')
        with self.assertRaises(AdobeOutcomeUnknown):
            self.bridge.create_canvas(16, 16, 'unknown.psd')
        marker = json.loads((self.root / 'pending.json').read_text())
        self.assertEqual(marker['action'], 'create_canvas')
        self.assertEqual(self.bridge._session(marker['session_id'])['state'], 'unknown')
        count = len(self.bridge.requests)
        self.bridge.error = None
        with self.assertRaises(AdobeOutcomeUnknown):
            self.bridge.create_canvas(16, 16, 'replay.psd')
        self.assertEqual(len(self.bridge.requests), count)

    def test_canvas_wrong_native_properties_becomes_unknown(self):
        self.bridge._native = lambda request, state, directory: {
            'name': 'wrong.psd', 'path': request['document_path'], 'native_id': 42,
            'identity': 'document_id_and_retained_reference', 'saved': True,
            'width': request['canvas_width'], 'height': request['canvas_height'],
            'units': 'pixels', 'color_profile': '', 'color_space': 'DocumentMode.RGB',
            'bit_depth': 'BitsPerChannelType.EIGHT', 'layers': [], 'paths': []}
        with self.assertRaises(AdobeOutcomeUnknown):
            self.bridge.create_canvas(24, 24, 'wrong-profile.psd')
        marker = json.loads((self.root / 'pending.json').read_text())
        self.assertEqual(marker['action'], 'create_canvas')
        self.assertEqual(self.bridge._session(marker['session_id'])['state'], 'unknown')


class PhotoshopProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_photoshop_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder) / 'workspace'
            workspace.mkdir()
            parameters = StdioServerParameters(command=sys.executable,
                args=['-m', 'cavi_adobe_mcp.photoshop.server'],
                env={**os.environ, 'CAVI_ADOBE_WORKSPACE': str(workspace)})
            async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as client:
                await client.initialize()
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                self.assertEqual(set(tools), PHOTOSHOP_TOOLS)
                self.assertFalse((workspace / '.adobe-authoring').exists())
                for name, tool in tools.items():
                    with self.subTest(tool=name):
                        self.assertNotIn('application', tool.input_schema.get('properties', {}))
                        self.assertNotIn('application', tool.input_schema.get('required', []))
                self.assertEqual(set(tools['photoshop_create_canvas'].input_schema['required']),
                                 {'width', 'height', 'destination'})
                self.assertEqual(set(tools['photoshop_resize_image'].input_schema['required']),
                                 {'session_id', 'width', 'height'})
                self.assertNotIn('kind', tools['photoshop_export'].input_schema['properties'])
                for name in ('photoshop_status', 'photoshop_inspect', 'photoshop_fonts'):
                    self.assertTrue(tools[name].annotations.read_only_hint, name)
                for name, required in (('photoshop_generative_fill', {'session_id', 'prompt'}),
                                       ('photoshop_remove_background', {'session_id', 'layer_path', 'expected_name'}),
                                       ('photoshop_generative_upscale', {'session_id'}),
                                       ('photoshop_resize_image', {'session_id', 'width', 'height'})):
                    with self.subTest(tool=name):
                        tool = tools[name]
                        self.assertEqual(set(tool.input_schema['required']), required)
                        self.assertFalse(tool.annotations.read_only_hint)
                        self.assertTrue(tool.annotations.destructive_hint)
                self.assertFalse(tools['photoshop_create_canvas'].annotations.read_only_hint)
                self.assertTrue(tools['photoshop_generative_fill'].annotations.open_world_hint)
                self.assertTrue(tools['photoshop_generative_upscale'].annotations.open_world_hint)
                self.assertFalse(tools['photoshop_remove_background'].annotations.open_world_hint)
                rejected_fill = await client.call_tool('photoshop_generative_fill', {
                    'session_id': '0' * 32, 'prompt': 'x' * 1001})
                self.assertTrue(rejected_fill.is_error)
                self.assertEqual(rejected_fill.structured_content['error']['outcome'], 'rejected')
                rejected = await client.call_tool('photoshop_create_canvas', {
                    'width': True, 'height': 256, 'destination': 'output/canvas.psd'})
                self.assertTrue(rejected.is_error)
                self.assertEqual(rejected.structured_content['error']['outcome'], 'rejected')
                rejected_resize = await client.call_tool('photoshop_resize_image', {
                    'session_id': '0' * 32, 'width': True, 'height': 768})
                self.assertTrue(rejected_resize.is_error)
                stale = await client.call_tool('photoshop_status', {'application': 'photoshop'})
                self.assertTrue(stale.is_error)
                self.assertFalse((workspace / '.adobe-authoring').exists())


@unittest.skipUnless(sys.platform == 'darwin', 'native Adobe preconditions are checked on macOS')
class PhotoshopPreconditionProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_not_running_application_is_a_rejected_outcome_with_its_message(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            workspace, app = root / 'workspace', root / 'Fixture.app'
            workspace.mkdir()
            (app / 'Contents' / 'MacOS').mkdir(parents=True)
            (app / 'Contents' / 'MacOS' / 'Fixture').write_bytes(b'')
            (app / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': 'com.adobe.Photoshop', 'CFBundleExecutable': 'Fixture',
                'CFBundleShortVersionString': '1.0'}))
            (workspace / 'source.png').write_bytes(png_bytes())
            parameters = StdioServerParameters(command=sys.executable,
                args=['-m', 'cavi_adobe_mcp.photoshop.server'],
                env={**os.environ, 'CAVI_ADOBE_WORKSPACE': str(workspace),
                     'CAVI_PHOTOSHOP_APP': str(app)})
            async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as client:
                await client.initialize()
                for name, arguments in (
                        ('photoshop_open_copy', {'source': 'source.png', 'destination': 'copy.psd'}),
                        ('photoshop_inspect', {})):
                    with self.subTest(tool=name):
                        result = await client.call_tool(name, arguments)
                        self.assertTrue(result.is_error)
                        self.assertEqual(result.structured_content['error']['outcome'], 'rejected')
                        self.assertIn('App is not running', result.content[0].text)
                self.assertFalse((workspace / 'copy.psd').exists())


if __name__ == '__main__':
    unittest.main()
