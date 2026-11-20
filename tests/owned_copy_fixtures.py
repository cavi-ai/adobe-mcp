"""Fake native transports; they exercise the Python boundary and the durable journal only."""

import struct
import zlib
from contextlib import contextmanager
from pathlib import Path

from cavi_adobe_mcp.illustrator.bridge import IllustratorAuthoring
from cavi_adobe_mcp.photoshop.bridge import PhotoshopAuthoring

SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><rect width="1" height="1"/></svg>'


def png_bytes(profile=True):
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
            + (chunk(b'sRGB', b'\x00') if profile else b'')
            + chunk(b'IDAT', zlib.compress(b'\x00\x2a\x80\xff\xff')) + chunk(b'IEND', b''))


class ContractTransport:
    """Replaces the osascript transport and the process-global lock of an owned-copy host."""

    def __init__(self, workspace):
        super().__init__(workspace)
        self.requests = []
        self.error = None
        self.fonts_receipt = []
        self.text_layers = []
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
            destination = Path(request['destination'])
            if request['kind'] == 'svg':
                destination.write_text(SVG)
            else:
                destination.write_bytes(png_bytes())
        if request['action'] == 'inspect':
            return {'documents': []}
        if request['action'] == 'fonts':
            return {'fonts': list(self.fonts_receipt)}
        if request['action'] == 'text_fonts':
            return {'layers': list(self.text_layers)}
        snapshot = {'name': Path(request['document_path']).name, 'path': request['document_path'],
                'native_id': 42, 'identity': 'document_id_and_retained_reference', 'saved': True,
                'width': 1, 'height': 1, 'units': 'pixels', 'color_profile': None,
                'color_space': 'DocumentMode.RGB', 'bit_depth': 'BitsPerChannelType.EIGHT',
                'export_profile': 'sRGB IEC61966-2.1', 'layers': [], 'paths': []}
        if request['action'] == 'create_canvas':
            snapshot.update(width=request['canvas_width'], height=request['canvas_height'],
                            bit_depth='BitsPerChannelType.' + ('SIXTEEN' if request['canvas_bit_depth'] == 16 else 'EIGHT'),
                            color_profile='sRGB IEC61966-2.1')
        if request['action'] == 'resize_image':
            snapshot.update(width=request['resize_width'], height=request['resize_height'])
        if request['action'] in ('generative_fill', 'generative_upscale'):
            snapshot['changed_layers'] = [{'path': [0], 'name': 'Generated ' + request['action']}]
        return snapshot


class ContractBridge(ContractTransport, PhotoshopAuthoring):
    """Photoshop host with a fake transport."""


class IllustratorContractBridge(ContractTransport, IllustratorAuthoring):
    """Illustrator host with a fake transport."""
