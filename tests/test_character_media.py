"""PNG/WAV contract fixtures are not native Character Animator export evidence."""

import json
import tempfile
import unittest
import wave
from pathlib import Path

from PIL import Image

from cavi_adobe_mcp import art_media, character_media


def write(path, value):
    path.write_text(json.dumps(value))


def reference(path):
    return {'path': path.name, 'sha256': art_media.digest(path)}


def fixture(root):
    frames = []
    for name, color in (('PoseZ', (250, 0, 0, 42)), ('PoseA', (0, 250, 0, 0))):
        path = root / (name + '.png')
        with Image.new('RGBA', (4, 3), color) as image:
            image.save(path)
        frames.append({'name': name, **reference(path)})
    audio_path = root / 'import-timing-fixture.wav'
    # Synthetic silence tests file import/timing only; it is not voice or performance.
    with wave.open(str(audio_path), 'wb') as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(bytes(4800 * 2 * 2))
    project = root / 'fixture.chproj'
    project.write_text('fixture project source')
    xml = root / 'fixture.xml'
    xml.write_text('<xmeml version="5"/>')
    recipe = {'schema': 'cavi.character-export.v1', 'version': 1, 'name': 'Character', 'loop': False,
              'alpha': 'straight', 'frame_rate': {'numerator': 24000, 'denominator': 1001}, 'frames': frames,
              'audio': {**reference(audio_path), 'start': .01, 'clip_in_samples': 1200, 'duration_samples': 2400},
              'source_roles': [{'role': 'project', **reference(project)}, {'role': 'export_xml', **reference(xml)}]}
    write(root / 'export-input.json', recipe)
    return recipe


class CharacterMediaTests(unittest.TestCase):
    def test_inspection_preserves_explicit_order_and_labels_caller_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture(root)
            result = character_media.inspect_export(root, 'export-input.json')
            self.assertEqual([f['name'] for f in result['frames']], ['PoseZ', 'PoseA'])
            self.assertEqual(result['frame_rate_source'], 'caller_declared')
            self.assertEqual(result['frame_order_source'], 'explicit_recipe')
            self.assertEqual(result['audio_timing_source'], 'explicit_samples')
            self.assertEqual(result['xml_timing'], 'not_interpreted')
            self.assertEqual(result['native_export'], 'not_verified')
            self.assertEqual((result['audio']['sample_rate'], result['audio']['channels'],
                              result['audio']['sample_width'], result['audio']['samples']), (48000, 2, 2, 4800))
            self.assertFalse(result['launches_applications'])
            self.assertFalse((root / 'handoff').exists())

    def test_inspection_rejects_source_drift_and_intake_pins_original_sources(self):
        for path in ('PoseZ.png', 'fixture.chproj', 'fixture.xml', 'import-timing-fixture.wav'):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                fixture(root)
                original = (root / path).read_bytes()
                (root / path).write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'source changed'):
                    character_media.ingest_export(root, 'export-input.json', 'handoff')
                self.assertFalse((root / 'handoff').exists())
                (root / path).write_bytes(original)
                result = character_media.ingest_export(root, 'export-input.json', 'handoff')
                (root / path).write_bytes(b'changed after intake')
                with self.assertRaises(ValueError):
                    art_media.validate(root, result['media_receipt']['path'])

    def test_rejects_ambiguous_frames_unbounded_metadata_and_remote_paths(self):
        mutations = ('duplicate_path', 'duplicate_name', 'long_name', 'missing_hash', 'bad_rate',
                     'duration', 'remote', 'traversal', 'unknown_role', 'xml_mode', 'alpha', 'bool_version', 'role_type')
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                spec = fixture(root)
                if mutation == 'duplicate_path': spec['frames'][1] = {**spec['frames'][0], 'name': 'Other'}
                if mutation == 'duplicate_name': spec['frames'][1]['name'] = spec['frames'][0]['name']
                if mutation == 'long_name': spec['frames'][0]['name'] = 'X' * 49
                if mutation == 'missing_hash': del spec['frames'][0]['sha256']
                if mutation == 'bad_rate': spec['frame_rate']['numerator'] = True
                if mutation == 'duration': spec['audio']['duration_samples'] = 4800
                if mutation == 'remote': spec['frames'][0]['path'] = 'https://example.com/PoseZ.png'
                if mutation == 'traversal': spec['frames'][0]['path'] = '../PoseZ.png'
                if mutation == 'unknown_role': spec['source_roles'][0]['role'] = 'credentials'
                if mutation == 'xml_mode': spec['xml'] = 'fixture.xml'
                if mutation == 'alpha': spec['alpha'] = 'premultiplied'
                if mutation == 'bool_version': spec['version'] = True
                if mutation == 'role_type': spec['source_roles'][0]['role'] = []
                write(root / 'export-input.json', spec)
                with self.assertRaises(ValueError):
                    character_media.ingest_export(root, 'export-input.json', 'handoff')
                self.assertFalse((root / 'handoff').exists())

    def test_rejects_invalid_or_truncated_audio_and_bad_sample_offsets(self):
        for mutation in ('truncated', 'float_pcm', 'bad_samples', 'bool_start', 'past_end', 'byte_budget'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                spec = fixture(root)
                path = root / spec['audio']['path']
                raw = path.read_bytes()
                if mutation == 'truncated': path.write_bytes(raw[:-4])
                if mutation == 'float_pcm': path.write_bytes(raw[:20] + b'\x03\x00' + raw[22:])
                if mutation == 'bad_samples': spec['audio']['clip_in_samples'] = .5
                if mutation == 'bool_start': spec['audio']['start'] = True
                if mutation == 'past_end': spec['audio']['clip_in_samples'] = 4800
                if mutation == 'byte_budget':
                    with path.open('wb') as stream:
                        stream.truncate(64 * 1024 * 1024 + 1)
                else:
                    spec['audio']['sha256'] = art_media.digest(path)
                write(root / 'export-input.json', spec)
                with self.assertRaises(ValueError):
                    character_media.ingest_export(root, 'export-input.json', 'handoff')
                self.assertFalse((root / 'handoff').exists())

    def test_export_without_audio_or_xml_remains_explicit_and_pinned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            spec = fixture(root)
            del spec['audio']
            spec['source_roles'] = []
            write(root / 'export-input.json', spec)
            result = character_media.ingest_export(root, 'export-input.json', 'handoff')
            self.assertIsNone(result['cutscene_audio'])
            self.assertIsNone(result['audio_timing_source'])
            self.assertEqual(result['xml_timing'], 'not_interpreted')
            self.assertEqual(result['frame_rate_source'], 'caller_declared')
            saved = json.loads((root / result['handoff']['path']).read_text())
            self.assertEqual(saved['inputs']['export-input.json'], art_media.digest(root / 'export-input.json'))


if __name__ == '__main__':
    unittest.main()
