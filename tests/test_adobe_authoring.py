"""Unit tests for file, ownership and outcome contracts; these are not native app proof."""

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from cavi_adobe_mcp.adobe_authoring import (
    AdobeAuthoring,
    AdobeCommandRejected,
    AdobeOutcomeUnknown,
    _native_snapshot,
    _png,
    _svg,
    digest,
    validate_edits,
)


def png_bytes(profile=True):
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
            + (chunk(b'sRGB', b'\x00') if profile else b'')
            + chunk(b'IDAT', zlib.compress(b'\x00\x2a\x80\xff\xff')) + chunk(b'IEND', b''))


class ContractBridge(AdobeAuthoring):
    """Fake transport tests only the Python boundary and durable journal behavior."""
    def __init__(self, workspace, app='photoshop'):
        super().__init__(workspace, app)
        self.requests = []
        self.error = None
        self.process = {'pid': 123, 'started': 'fixture'}

    def status(self):
        return {'app': self.app, 'running': True, 'process': self.process,
                'application': '/fixture/app', 'version': 'fixture'}

    @contextmanager
    def _locked(self):
        yield self.workspace / 'pending.json'

    def _native(self, request, state, directory):
        self.requests.append(request)
        if self.error:
            raise self.error
        if request['action'] == 'open_copy':
            Path(request['document_path']).write_bytes(Path(request['input_copy']).read_bytes())
        if request['action'] == 'create_canvas':
            Path(request['document_path']).write_bytes(b'fixture-native-psd')
        if request['action'] == 'export':
            Path(request['destination']).write_bytes(png_bytes())
        if request['action'] == 'inspect':
            return {'documents': []}
        snapshot = {'name': Path(request['document_path']).name, 'path': request['document_path'],
                'native_id': 42, 'identity': 'document_id_and_retained_reference', 'saved': True,
                'width': 1, 'height': 1, 'units': 'pixels', 'color_profile': None,
                'color_space': 'DocumentMode.RGB', 'bit_depth': 'BitsPerChannelType.EIGHT',
                'export_profile': 'sRGB IEC61966-2.1', 'layers': [], 'paths': []}
        if request['action'] == 'create_canvas':
            snapshot.update(width=request['canvas_width'], height=request['canvas_height'],
                            bit_depth='BitsPerChannelType.' + ('SIXTEEN' if request['canvas_bit_depth'] == 16 else 'EIGHT'),
                            color_profile='sRGB IEC61966-2.1')
        return snapshot


class AdobeAuthoringTests(unittest.TestCase):
    def test_canvas_depth_is_explicit_and_rejects_unsupported_values(self):
        result = self.bridge.create_canvas(32, 32, 'selected.psd', bit_depth=8)
        self.assertEqual(result['document']['bit_depth'], 'BitsPerChannelType.EIGHT')
        for depth in (32, True, 8.0, None):
            with self.subTest(depth=depth), self.assertRaises(ValueError):
                self.bridge.create_canvas(32, 32, 'invalid.psd', bit_depth=depth)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bridge = ContractBridge(self.root)
        self.source = self.root / 'source.png'
        self.source.write_bytes(png_bytes())

    def session(self):
        result = self.bridge.open_copy('source.png', 'working.psd')
        return result['session_id']

    def test_copy_preserves_source_uses_private_input_and_separate_native_token(self):
        source = digest(self.source)
        result = self.bridge.open_copy('source.png', 'working.psd')
        request = self.bridge.requests[0]
        self.assertEqual(source, digest(self.source))
        self.assertNotEqual(request['input_copy'], str(self.source))
        self.assertEqual(digest(Path(request['input_copy'])), source)
        self.assertEqual(result['copy']['sha256'], digest(self.root / 'working.psd'))
        self.assertNotIn(request['ownership_token'], json.dumps(result))
        self.assertNotEqual(request['ownership_token'], result['session_id'])
        inspected = self.bridge.inspect_session(result['session_id'])
        self.assertNotIn('ownership_token', inspected['session'])

    def test_existing_destination_is_rejected_before_native_command(self):
        target = self.root / 'working.psd'
        target.write_bytes(b'keep')
        with self.assertRaises(ValueError):
            self.bridge.open_copy('source.png', 'working.psd')
        self.assertEqual(target.read_bytes(), b'keep')
        self.assertEqual(self.bridge.requests, [])

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

    def test_canvas_arguments_and_app_are_bounded_before_native_dispatch(self):
        for width, height in ((0, 1), (2881, 1), (True, 1), (1, 1.5)):
            with self.subTest(width=width, height=height), self.assertRaises(ValueError):
                self.bridge.create_canvas(width, height, 'bad.psd')
        illustrator = ContractBridge(self.root, app='illustrator')
        with self.assertRaisesRegex(ValueError, 'only in Photoshop'):
            illustrator.create_canvas(1, 1, 'bad.psd')
        self.assertEqual(self.bridge.requests, [])

    def test_canvas_native_rejection_is_recorded_rejected_and_not_owned(self):
        self.bridge.error = AdobeCommandRejected('Photoshop preflight rejected canvas')
        with self.assertRaises(AdobeCommandRejected):
            self.bridge.create_canvas(16, 16, 'rejected.psd')
        marker_path = self.root / '.adobe-authoring'
        packet_path = next(marker_path.glob('*.json'))
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

    def test_paths_reject_traversal_external_paths_and_symlinks(self):
        (self.root / 'alias.png').symlink_to(self.source)
        for source in ('../source.png', '/tmp/outside.png', 'alias.png'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.bridge.open_copy(source, 'working.psd')
        (self.root / 'redirect').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.bridge.open_copy('source.png', 'redirect/working.psd')
        self.assertFalse(self.bridge.requests)

    def test_unknown_copy_outcome_blocks_mutations_and_retains_journal(self):
        self.bridge.error = AdobeOutcomeUnknown('no native receipt')
        with self.assertRaises(AdobeOutcomeUnknown):
            self.bridge.open_copy('source.png', 'working.psd')
        marker = json.loads((self.root / 'pending.json').read_text())
        packet = self.bridge._session(marker['session_id'])
        self.assertEqual(packet['state'], 'unknown')
        count = len(self.bridge.requests)
        self.bridge.error = None
        with self.assertRaises(AdobeOutcomeUnknown):
            self.bridge.open_copy('source.png', 'other.psd')
        self.assertEqual(len(self.bridge.requests), count)
        self.assertTrue(Path(marker['diagnostics']).is_dir())
        self.addCleanup(lambda: shutil.rmtree(marker['diagnostics']))

    def test_read_timeout_blocks_later_mutations(self):
        self.bridge.error = AdobeOutcomeUnknown('inspect timed out')
        with self.assertRaises(AdobeOutcomeUnknown):
            self.bridge.inspect()
        self.bridge.error = None
        with self.assertRaises(AdobeOutcomeUnknown):
            self.bridge.open_copy('source.png', 'working.psd')
        self.assertFalse((self.root / 'working.psd').exists())
        pending = json.loads((self.root / 'pending.json').read_text())
        self.addCleanup(lambda: shutil.rmtree(pending['diagnostics']))

    def test_native_preflight_rejection_is_distinct_from_unknown(self):
        session = self.session()
        self.bridge.error = AdobeCommandRejected('target is locked')
        with self.assertRaises(ValueError):
            self.bridge.add_group(session, 'Head')
        self.assertFalse((self.root / 'pending.json').exists())
        self.assertEqual(self.bridge._session(session)['state'], 'owned')

    def test_source_or_copy_change_prevents_native_mutation(self):
        session = self.session()
        count = len(self.bridge.requests)
        original = self.source.read_bytes()
        self.source.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Source changed'):
            self.bridge.add_group(session, 'Head')
        self.source.write_bytes(original)
        (self.root / 'working.psd').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Working copy changed'):
            self.bridge.add_group(session, 'Head')
        self.assertEqual(len(self.bridge.requests), count)

    def test_process_change_prevents_session_reuse(self):
        session = self.session()
        self.bridge.process = {'pid': 456, 'started': 'new'}
        with self.assertRaisesRegex(ValueError, 'process changed'):
            self.bridge.add_group(session, 'Head')

    def test_session_record_cannot_redirect_files(self):
        session = self.session()
        packet = self.bridge._session(session)
        packet['copy'] = '/tmp/not-a-task-owned.psd'
        self.bridge._record(packet)
        with self.assertRaises(ValueError):
            self.bridge.add_group(session, 'Head')

    def test_png_placement_names_group_and_position_are_data(self):
        session = self.session()
        name = 'Mouth "happy"; $.writeln("data")'
        self.bridge.place_image(session, 'source.png', name, [12, 34],
                                group_path=[0], expected_group_name='+Head')
        request = self.bridge.requests[-1]
        self.assertEqual(request['name'], name)
        self.assertEqual(request['group_path'], [0])
        self.assertEqual(request['expected_group_name'], '+Head')
        self.assertNotEqual(request['source'], str(self.source))
        self.assertEqual(request['position'], [12, 34])

    def test_groups_require_exact_parent_path_and_name(self):
        session = self.session()
        self.bridge.add_group(session, '+Head', parent_path=[0], expected_parent_name='+Puppet')
        request = self.bridge.requests[-1]
        self.assertEqual(request['group_path'], [0])
        for path, name in (([0], None), (None, 'Head'), ([-1], 'Head')):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.bridge.add_group(session, 'Mouth', parent_path=path, expected_parent_name=name)

    def test_edit_validation_rejects_unbounded_or_ambiguous_targets(self):
        valid = {'path': [0, 1], 'expected_name': 'Mask', 'opacity': 30, 'visible': False}
        self.assertEqual(validate_edits([valid]), [valid])
        for edits in ([], [valid, valid], [{**valid, 'path': [True]}], [{**valid, 'opacity': float('nan')}],
                      [{**valid, 'visible': 1}], [{**valid, 'script': 'bad'}],
                      [{'path': [0], 'expected_name': 'Mask'}]):
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                validate_edits(edits)

    def test_export_has_real_file_hashes_and_no_ownership_secret(self):
        session = self.session()
        token = self.bridge._session(session)['ownership_token']
        result = self.bridge.export(session, 'mask.png', usage='data')
        self.assertEqual(result['artifact']['sha256'], digest(self.root / 'mask.png'))
        self.assertFalse(result['artifact']['srgb'])
        self.assertEqual(result['artifact']['removed_color_chunks'], ['sRGB'])
        self.assertEqual(result['receipt']['sha256'], digest(self.root / 'mask.png.receipt.json'))
        self.assertNotIn(token, json.dumps(result))
        self.assertNotEqual(result['native_export_sha256'], result['artifact']['sha256'])

    def test_export_race_cannot_overwrite_an_existing_destination(self):
        session = self.session()
        original = self.bridge._native
        def race(request, state, directory):
            result = original(request, state, directory)
            if request['action'] == 'export':
                (self.root / 'mask.png').write_bytes(b'other owner')
            return result
        self.bridge._native = race
        with self.assertRaises(AdobeOutcomeUnknown) as caught:
            self.bridge.export(session, 'mask.png', usage='data')
        self.assertIsInstance(caught.exception.__cause__, FileExistsError)
        self.assertEqual((self.root / 'mask.png').read_bytes(), b'other owner')
        self.assertTrue((self.root / 'pending.json').exists())
        self.assertEqual(self.bridge._session(session)['state'], 'unknown')

    def test_failed_export_postprocessing_blocks_blind_retries(self):
        session = self.session()
        with (patch('cavi_adobe_mcp.adobe_authoring._png', side_effect=AdobeOutcomeUnknown('not valid PNG')),
              self.assertRaises(AdobeOutcomeUnknown)):
            self.bridge.export(session, 'bad.png')
        count = len(self.bridge.requests)
        with self.assertRaises(AdobeOutcomeUnknown):
            self.bridge.export(session, 'retry.png')
        self.assertEqual(len(self.bridge.requests), count)
        self.assertEqual(self.bridge._session(session)['state'], 'unknown')

    def test_missing_copy_after_successful_dispatch_is_unknown(self):
        session = self.session()
        source_hash = digest(self.source)
        native = self.bridge._native

        def dispatch_then_remove_copy(request, state, directory):
            result = native(request, state, directory)
            copy = Path(request['document_path'])
            copy.write_bytes(b'simulated saved document')
            copy.unlink()
            return result

        self.bridge._native = dispatch_then_remove_copy
        with self.assertRaises(AdobeOutcomeUnknown) as caught:
            self.bridge.add_group(session, 'Head')
        self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)
        self.assertEqual(self.bridge.requests[-1]['action'], 'add_group')
        self.assertEqual(digest(self.source), source_hash)
        self.assertEqual(self.bridge._session(session)['state'], 'unknown')
        marker = json.loads((self.root / 'pending.json').read_text())
        self.addCleanup(lambda: shutil.rmtree(marker['diagnostics']))
        count = len(self.bridge.requests)
        with self.assertRaises(AdobeOutcomeUnknown):
            self.bridge.add_group(session, 'Head')
        self.assertEqual(len(self.bridge.requests), count)

    def test_finalize_value_error_after_successful_export_is_unknown(self):
        session = self.session()
        error = ValueError('export receipt validation failed')
        with (patch('cavi_adobe_mcp.adobe_authoring._png', side_effect=error),
              self.assertRaises(AdobeOutcomeUnknown) as caught):
            self.bridge.export(session, 'mask.png')
        self.assertIs(caught.exception.__cause__, error)
        request = self.bridge.requests[-1]
        self.assertEqual(request['action'], 'export')
        self.assertEqual(Path(request['destination']).read_bytes(), png_bytes())
        self.assertEqual(self.bridge._session(session)['state'], 'unknown')
        marker = json.loads((self.root / 'pending.json').read_text())
        self.addCleanup(lambda: shutil.rmtree(marker['diagnostics']))
        count = len(self.bridge.requests)
        with self.assertRaises(AdobeOutcomeUnknown):
            self.bridge.export(session, 'retry.png')
        self.assertEqual(len(self.bridge.requests), count)

    def test_interrupt_retains_its_identity_and_unknown_journal(self):
        session = self.session()
        error = KeyboardInterrupt('cancelled')
        self.bridge.error = error
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.bridge.add_group(session, 'Head')
        self.assertIs(caught.exception, error)
        self.assertEqual(self.bridge._session(session)['state'], 'unknown')
        marker = json.loads((self.root / 'pending.json').read_text())
        self.addCleanup(lambda: shutil.rmtree(marker['diagnostics']))

    def test_existing_export_receipt_is_rejected_before_dispatch(self):
        session = self.session()
        (self.root / 'mask.png.receipt.json').write_text('keep')
        count = len(self.bridge.requests)
        with self.assertRaises(ValueError):
            self.bridge.export(session, 'mask.png')
        self.assertEqual(len(self.bridge.requests), count)

    def test_svg_format_app_and_data_guards_run_before_native_call(self):
        session = self.session()
        for kind, usage in (('svg', 'color'), ('svg', 'data'), ('png', 'normal'), ('jpeg', 'color')):
            with self.subTest(kind=kind, usage=usage), self.assertRaises(ValueError):
                self.bridge.export(session, 'out.' + kind, kind=kind, usage=usage)

    def test_data_png_removes_color_metadata_without_changing_compressed_pixels(self):
        path = self.root / 'data.png'
        original = png_bytes()
        path.write_bytes(original)
        _png(path, data=True)
        self.assertNotIn(b'sRGB', path.read_bytes())
        self.assertIn(zlib.compress(b'\x00\x2a\x80\xff\xff'), path.read_bytes())

    def test_color_metadata_is_added_only_for_confirmed_native_srgb(self):
        path = self.root / 'color.png'
        path.write_bytes(png_bytes(False))
        with self.assertRaises(AdobeOutcomeUnknown):
            _png(path)
        _png(path, color_profile='sRGB IEC61966-2.1')
        self.assertIn(b'sRGB', path.read_bytes())
        _png(path)

    def test_png_crc_truncation_and_non_png_fail(self):
        path = self.root / 'bad.png'
        for content in (b'no', png_bytes()[:-1], png_bytes().replace(b'sRGB', b'bad!')):
            path.write_bytes(content)
            with self.subTest(content=content[:10]), self.assertRaises(AdobeOutcomeUnknown):
                _png(path, data=True)

    def test_svg_rejects_external_dependencies_and_active_content(self):
        path = self.root / 'source.svg'
        for content in ('<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///tmp/private.png"/></svg>',
                        '<svg><script>bad</script></svg>', '<svg onload="bad"/>',
                        '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///tmp/private">]><svg/>',
                        '<svg><path fill="url(https://example.com/a)"/></svg>'):
            path.write_text(content)
            with self.subTest(content=content), self.assertRaises(AdobeOutcomeUnknown):
                _svg(path)
        path.write_text('<svg><path fill="url(\'#local\')"/><image href="data:image/png;base64,AA=="/></svg>')
        self.assertTrue(_svg(path)['self_contained'])

    def test_native_transport_serializes_values_without_executing_caller_code(self):
        request = {"action": "add_group", "operation_id": "operation",
                   "name": 'café 東京 "; throw Error("caller code"); //\u2028'}
        response = {"ok": True, "operation_id": "operation", "result": {"observed": True}}
        state = self.bridge.status()
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / 'München Ω'
            directory.mkdir()
            with patch('cavi_adobe_mcp.adobe_authoring.subprocess.run', return_value=subprocess.CompletedProcess(
                    [], 0, stdout=json.dumps(response), stderr='')) as process:
                result = AdobeAuthoring._native(self.bridge, request, state, directory)
            script = next(directory.glob('*.jsx')).read_text()
            encoded = json.loads(script.split('var CAVI_REQUEST = ', 1)[1].split(';\n', 1)[0])
            self.assertEqual({key: encoded[key] for key in request}, request)
            self.assertEqual(encoded['completion_path'], str(directory / 'completion.json'))
            self.assertEqual(result, {"observed": True})
            self.assertEqual(process.call_args.args[0][0], '/usr/bin/osascript')
            self.assertIn('to do javascript (item 2 of argv)', process.call_args.args[0][2])
            self.assertEqual(process.call_args.args[0][3], str(next(directory.glob('*.jsx'))))
            self.assertEqual(process.call_args.args[0][4], script)
            self.assertIn('caf\\u00e9 \\u6771\\u4eac', script)
            self.assertIn('caller code', script)
            self.assertEqual(Path(process.call_args.args[0][3]).parent.name, 'München Ω')
            self.assertFalse(process.call_args.kwargs.get('shell', False))

    def test_successful_same_process_inspect_reconciles_only_prior_read_marker(self):
        pending = self.root / 'pending.json'
        diagnostics = self.root / 'old-inspect-diagnostics'
        diagnostics.mkdir()
        (diagnostics / 'driver.jsx').write_text('preserve')
        operation_id = '1' * 32
        pending.write_text(json.dumps({
            'action': 'inspect', 'state': 'unknown', 'operation_id': operation_id,
            'workspace': str(self.root), 'process': self.bridge.process,
            'diagnostics': str(diagnostics)}))

        result = self.bridge.inspect()

        self.assertIsNone(result['pending_operation'])
        self.assertFalse(pending.exists())
        archive = self.root / '.adobe-authoring' / f'reconciled-read-{operation_id}.json'
        record = json.loads(archive.read_text())
        self.assertEqual(record['resolution'], 'read_only_reconciled')
        self.assertEqual(record['prior']['diagnostics'], str(diagnostics))
        self.assertEqual((diagnostics / 'driver.jsx').read_text(), 'preserve')

    def test_successful_inspect_does_not_reconcile_unknown_write_marker(self):
        pending = self.root / 'pending.json'
        prior = {'action': 'add_group', 'state': 'unknown', 'operation_id': '2' * 32,
                 'workspace': str(self.root), 'process': self.bridge.process}
        pending.write_text(json.dumps(prior))
        result = self.bridge.inspect()
        self.assertEqual(result['pending_operation'], prior)
        self.assertTrue(pending.exists())

    def test_native_svg_export_removes_external_dtd_without_fetching(self):
        path = self.root / 'export.svg'
        path.write_text('<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
                        '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd"><svg/>')
        with self.assertRaises(AdobeOutcomeUnknown):
            _svg(path)
        self.assertTrue(_svg(path, native_export=True)['self_contained'])
        self.assertNotIn(b'<!DOCTYPE', path.read_bytes())

    def test_global_cooperative_lock_prevents_another_instance(self):
        # Exercise the real OS lock without using the native transport or a user's app journal.
        bridge = AdobeAuthoring(self.root, 'photoshop')
        with (patch('cavi_adobe_mcp.adobe_authoring.tempfile.gettempdir', return_value=str(self.root)),
              bridge._locked(), self.assertRaises(ValueError), bridge._locked()):
            self.fail('Second lock unexpectedly acquired')

    def test_constructor_and_rectangle_validation(self):
        with self.assertRaises(ValueError):
            AdobeAuthoring(self.root, 'other')
        with self.assertRaises(ValueError):
            AdobeAuthoring(self.root, 'photoshop', timeout=0)
        session = self.session()
        for bounds, rgb in (([0, 0, 0, 10], [0, 0, 0]), ([0, 0, 10, 10], [256, 0, 0]),
                            ([0, 0, float('inf'), 10], [0, 0, 0])):
            with self.assertRaises(ValueError):
                self.bridge.add_rectangle(session, 'Mask', bounds, rgb)

    def test_native_snapshot_excludes_operation_receipt_fields(self):
        snapshot = {'name': 'copy.psd', 'path': '/workspace/copy.psd', 'native_id': 7,
                    'identity': 'document_id_and_retained_reference', 'saved': True,
                    'width': 32, 'height': 32, 'units': 'pixels', 'color_profile': None,
                    'color_space': 'DocumentMode.RGB', 'bit_depth': 'BitsPerChannelType.EIGHT',
                    'layers': [], 'paths': [], 'changed_layers': [{'path': [0]}], 'export_path': '/workspace/export.png'}
        result = _native_snapshot(snapshot)
        self.assertNotIn('changed_layers', result)
        self.assertNotIn('export_path', result)
        self.assertEqual(result['native_id'], 7)

    def test_photoshop_profile_type_uses_color_profile_enum(self):
        driver = Path(__file__).resolve().parents[1] / 'src/cavi_adobe_mcp/adobe_authoring.jsx'
        source = driver.read_text()
        self.assertIn('d.colorProfileType === ColorProfile.NONE', source)
        self.assertIn('temporary.colorProfileType = ColorProfile.NONE', source)
        self.assertNotIn('ColorProfileType', source)


class AdobeCanvasProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_catalog_exposes_typed_photoshop_canvas_without_eager_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            workspace, data = root / 'workspace', root / 'data'
            workspace.mkdir()
            data.mkdir()
            parameters = StdioServerParameters(command=sys.executable,
                args=['-m', 'cavi_adobe_mcp.authoring_server'],
                env={**os.environ, 'CAVI_ADOBE_DATA': str(data),
                     'CAVI_ADOBE_WORKSPACE': str(workspace)})
            async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as client:
                await client.initialize()
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                canvas = tools['adobe_create_canvas']
                self.assertIn('width', canvas.input_schema['required'])
                self.assertIn('height', canvas.input_schema['required'])
                self.assertIn('destination', canvas.input_schema['required'])
                self.assertFalse(canvas.annotations.read_only_hint)
                self.assertFalse((workspace / '.adobe-authoring').exists())
                rejected = await client.call_tool('adobe_create_canvas', {
                    'width': True, 'height': 256, 'destination': 'output/canvas.psd'})
                self.assertTrue(rejected.is_error)
                self.assertEqual(rejected.structured_content['error']['outcome'], 'rejected')
                self.assertFalse((workspace / '.adobe-authoring').exists())


if __name__ == '__main__':
    unittest.main()
