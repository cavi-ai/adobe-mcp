"""Intake explicitly ordered Character Animator PNG/WAV exports without launching apps."""

import hashlib
import io
import math
import re
import wave
from pathlib import Path
from urllib.parse import urlsplit

from cavi_mcp_core.files import inside, parse_json, read_bytes

from . import art_media

SCHEMA = 'cavi.character-export.v1'
HASH = re.compile(r'[0-9a-f]{64}')
NAME = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,47}')
ROLES = {'project', 'puppet', 'performance', 'export_xml', 'export_receipt', 'artwork', 'dialogue_source'}


def _name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError('Character export names require 1..48 ASCII letters, digits, underscores or hyphens')
    return value


def _relative(workspace, path):
    return str(path.relative_to(Path(workspace).resolve(strict=True)))


def _reference(workspace, item, *, max_bytes=None):
    if (not isinstance(item, dict) or set(item) != {'path', 'sha256'}
            or not isinstance(item['path'], str) or not isinstance(item['sha256'], str)
            or not HASH.fullmatch(item['sha256'])):
        raise ValueError('Export sources require an exact path and SHA-256 reference')
    if urlsplit(item['path']).scheme:
        raise ValueError('Export sources require workspace file paths, not URLs')
    path = inside(workspace, item['path'])
    if not path.is_file():
        raise ValueError('Export sources must be regular workspace files')
    if max_bytes is not None and path.stat().st_size > max_bytes:
        raise ValueError('Character export media exceeds its encoded byte budget')
    if art_media.digest(path) != item['sha256']:
        raise ValueError('Character export source changed: ' + item['path'])
    return {'path': _relative(workspace, path), 'sha256': item['sha256']}


def _audio(workspace, item, frame_duration):
    if (not isinstance(item, dict)
            or set(item) != {'path', 'sha256', 'start', 'clip_in_samples', 'duration_samples'}):
        raise ValueError('Export audio requires a WAV reference and explicit start, clip-in and duration samples')
    reference = _reference(workspace, {key: item[key] for key in ('path', 'sha256')}, max_bytes=64 * 1024 * 1024)
    path = inside(workspace, reference['path'])
    if path.suffix.lower() != '.wav':
        raise ValueError('Character dialogue export must be PCM WAV')
    raw = read_bytes(path, 64 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != reference['sha256']:
        raise ValueError('Character dialogue WAV changed while reading')
    try:
        with wave.open(io.BytesIO(raw), 'rb') as stream:
            rate, channels, width, samples = (stream.getframerate(), stream.getnchannels(),
                                               stream.getsampwidth(), stream.getnframes())
            if (stream.getcomptype() != 'NONE' or stream.getformat() == wave.WAVE_FORMAT_IEEE_FLOAT
                    or not 8000 <= rate <= 192000
                    or channels not in (1, 2) or not 1 <= width <= 4 or samples <= 0
                    or samples / rate > 300
                    or len(stream.readframes(samples)) != samples * channels * width):
                raise ValueError('Character dialogue requires complete 8..192-kHz mono/stereo integer PCM, at most 300 seconds')
    except (wave.Error, EOFError) as exc:
        raise ValueError('Character dialogue export must be a valid uncompressed PCM WAV') from exc
    if (type(item['start']) not in (int, float) or not math.isfinite(item['start']) or not 0 <= item['start'] <= 300
            or type(item['clip_in_samples']) is not int or not 0 <= item['clip_in_samples'] < samples
            or type(item['duration_samples']) is not int or not 1 <= item['duration_samples'] <= samples
            or item['clip_in_samples'] + item['duration_samples'] > samples
            or item['start'] + item['duration_samples'] / rate > frame_duration + 1e-9):
        raise ValueError('Character dialogue sample range must fit the WAV and ordered frame duration')
    return {**reference, 'sample_rate': rate, 'channels': channels, 'sample_width': width, 'samples': samples,
            **{key: item[key] for key in ('start', 'clip_in_samples', 'duration_samples')}}


def inspect_export(workspace, recipe):
    """Inspect an explicit export recipe; order and FPS are caller-declared, never guessed."""
    root = Path(workspace).resolve(strict=True)
    path = inside(root, recipe)
    raw = read_bytes(path, 4 * 1024 * 1024)
    spec = parse_json(raw)
    if (not isinstance(spec, dict) or spec.get('schema') != SCHEMA
            or type(spec.get('version')) is not int or spec['version'] != 1
            or set(spec) - {'schema', 'version', 'name', 'loop', 'alpha', 'frame_rate', 'frames', 'audio', 'source_roles'}
            or type(spec.get('loop')) is not bool or spec.get('alpha') != 'straight'):
        raise ValueError('Declare a version 1 character export recipe with explicit frame order, FPS and straight alpha')
    name, fps = _name(spec.get('name')), art_media.rate(spec.get('frame_rate'))
    frames = spec.get('frames')
    if not isinstance(frames, list) or not 1 <= len(frames) <= 1000:
        raise ValueError('Character exports require 1..1000 explicitly ordered PNG frames')
    checked, references, paths = [], [], set()
    for frame in frames:
        if not isinstance(frame, dict) or set(frame) != {'name', 'path', 'sha256'}:
            raise ValueError('Each exported frame requires an explicit name, PNG path and SHA-256')
        label = _name(frame['name'])
        reference = _reference(root, {key: frame[key] for key in ('path', 'sha256')}, max_bytes=64 * 1024 * 1024)
        if Path(reference['path']).suffix.lower() != '.png' or reference['path'] in paths:
            raise ValueError('Exported frame paths must be unique PNGs; explicit repeated poses use separate exported files')
        paths.add(reference['path'])
        checked.append({'name': label, 'path': reference['path']})
        references.append(reference)
    checked = art_media.frames(root, checked, pinned=False)
    if any(frame['sha256'] != ref['sha256'] for frame, ref in zip(checked, references, strict=True)):
        raise ValueError('Character PNG changed during inspection')
    duration = len(checked) * fps['denominator'] / fps['numerator']
    if duration > 300:
        raise ValueError('Character export exceeds 300 seconds')
    roles = spec.get('source_roles', [])
    if not isinstance(roles, list) or len(roles) > 62:
        raise ValueError('Declare at most 62 explicit project/export source roles')
    source_roles = []
    for item in roles:
        if (not isinstance(item, dict) or set(item) != {'role', 'path', 'sha256'}
                or not isinstance(item['role'], str) or item['role'] not in ROLES):
            raise ValueError('Unsupported character export source role')
        reference = _reference(root, {key: item[key] for key in ('path', 'sha256')})
        if any(old['path'] == reference['path'] for old in source_roles):
            raise ValueError('Character export source roles must name distinct files')
        source_roles.append({'role': item['role'], **reference})
    recipe_ref = {'path': _relative(root, path), 'sha256': hashlib.sha256(raw).hexdigest()}
    audio = _audio(root, spec['audio'], duration) if 'audio' in spec else None
    inputs = {item['path']: item['sha256'] for item in [recipe_ref, *references, *source_roles]}
    if audio is not None:
        inputs[audio['path']] = audio['sha256']
    for source, expected in inputs.items():
        if art_media.digest(inside(root, source)) != expected:
            raise ValueError('Character export source changed during inspection: ' + source)
    return {'schema': SCHEMA, 'version': 1, 'status': 'inspected', 'name': name, 'loop': spec['loop'],
            'alpha': 'straight', 'recipe': recipe_ref, 'fps': fps, 'frames': checked, 'frame_count': len(checked),
            'duration': duration, 'frame_order_source': 'explicit_recipe', 'frame_rate_source': 'caller_declared',
            'audio_timing_source': 'explicit_samples' if audio is not None else None,
            'audio': audio, 'source_roles': source_roles, 'inputs': inputs,
            'xml_timing': 'not_interpreted', 'native_export': 'not_verified', 'launches_applications': False}


def ingest_export(workspace, recipe, directory):
    """Write a new media receipt and synchronized Unity audio descriptor from inspected exports."""
    root = Path(workspace).resolve(strict=True)
    target = inside(root, directory)
    if target.exists():
        raise FileExistsError('Character handoff output must be a new directory')
    data = inspect_export(root, recipe)
    source_paths = [data['recipe']['path'], *(item['path'] for item in data['source_roles'])]
    if data['audio'] is not None:
        source_paths.append(data['audio']['path'])
    source_paths = list(dict.fromkeys(source_paths))
    target.mkdir(parents=True, mode=0o700)
    media_recipe = target / 'media-input.json'
    art_media.write(media_recipe, {'version': 1, 'name': data['name'], 'fps': data['fps'], 'loop': data['loop'],
                                  'alpha': 'straight', 'frames': [{'name': frame['name'], 'path': frame['path']}
                                                               for frame in data['frames']], 'sources': source_paths})
    media = art_media.ingest(root, _relative(root, media_recipe), _relative(root, target / 'media'))
    for source, expected in data['inputs'].items():
        if art_media.digest(inside(root, source)) != expected:
            raise ValueError('Character export changed during intake; inspect the new output directory: ' + source)
    media_ref = {'path': _relative(root, Path(media['receipt'])), 'sha256': media['sha256']}
    audio = data['audio']
    if audio is not None:
        provenance = [media_ref, data['recipe'], *({key: item[key] for key in ('path', 'sha256')}
                                                for item in data['source_roles'])]
        audio = {**audio, 'sources': list({item['path']: item for item in provenance}.values())}
    result = {'schema': 'cavi.character-handoff.v1', 'version': 1, 'status': 'ingested',
              'name': data['name'], 'media_receipt': media_ref, 'cutscene_audio': audio,
              'fps': data['fps'], 'frame_count': data['frame_count'], 'duration': data['duration'],
              'frame_order_source': data['frame_order_source'], 'frame_rate_source': data['frame_rate_source'],
              'audio_timing_source': data['audio_timing_source'], 'source_roles': data['source_roles'],
              'inputs': data['inputs'], 'xml_timing': 'not_interpreted',
              'native_export': 'not_verified', 'launches_applications': False}
    output = target / 'handoff.json'
    art_media.write(output, result)
    return {**result, 'handoff': {'path': _relative(root, output), 'sha256': art_media.digest(output)}}
