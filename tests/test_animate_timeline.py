"""Timeline batches validate before native dispatch."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from cavi_adobe_mcp.animate_authoring import AnimateAuthoring, validate_timeline_edits


class TimelineTests(unittest.TestCase):
    def test_transform_and_symbol_contract(self):
        edits = [{'action': 'symbol', 'layer': 'Art', 'frame': 0, 'element': 0,
                  'name': 'Emblem', 'symbol_type': 'graphic'},
                 {'action': 'transform', 'layer': 'Art', 'frame': 12, 'element': 0,
                  'matrix': [1, 0, 0, 1, 120, 30]}]
        self.assertEqual(validate_timeline_edits(edits), edits)

    def test_unsafe_or_ambiguous_edits_reject_before_session(self):
        with tempfile.TemporaryDirectory() as folder:
            host = AnimateAuthoring(Path(folder))
            host._session = Mock(side_effect=AssertionError('must validate first'))
            for edit in [{'action':'eval','code':'anything'},
                         {'action':'transform','layer':'Art','frame':0,'element':0,
                          'matrix':[1,0,0,1,float('nan'),0]},
                         {'action':'tween','layer':'Art','frame':10,'end':5}]:
                with self.subTest(edit=edit), self.assertRaises(ValueError):
                    host.edit_timeline('id',[edit])
