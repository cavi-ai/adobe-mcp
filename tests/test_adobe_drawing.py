"""Typed drawing preflight and precision boundaries."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from cavi_adobe_mcp.adobe_authoring import AdobeAuthoring


class DrawingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.host = AdobeAuthoring(Path(self.temp.name), 'photoshop')
        self.host._owned_action = Mock(return_value={})
        self.points = [{'anchor': [1, 2], 'left': [1, 2], 'right': [4, 5]},
                       {'anchor': [8, 9], 'left': [6, 7], 'right': [8, 9]}]

    def test_curves_retain_handles_and_reject_nonfinite_before_dispatch(self):
        self.host.add_path('session', 'Curve', self.points, closed=False)
        packet = self.host._owned_action.call_args.args[2]
        self.assertEqual(packet['points'], self.points)
        self.host._owned_action.reset_mock()
        self.points[0]['left'][0] = float('nan')
        with self.assertRaises(ValueError):
            self.host.add_path('session', 'Curve', self.points)
        self.host._owned_action.assert_not_called()

    def test_text_uses_exact_font_and_preserves_unicode(self):
        self.host.add_text('session', 'Title', 'Garden — café', 'ExactPSName', [4, 20], 24, [30, 40, 50])
        packet = self.host._owned_action.call_args.args[2]
        self.assertEqual(packet['font'], 'ExactPSName')
        self.assertEqual(packet['text'], 'Garden — café')

    def test_paths_bind_exact_group_identity_before_dispatch(self):
        self.host.add_path('session', 'AhShape', self.points,
                           group_path=[0, 1], expected_group_name='Ah')
        packet = self.host._owned_action.call_args.args[2]
        self.assertEqual(packet['group_path'], [0, 1])
        self.assertEqual(packet['expected_group_name'], 'Ah')
        self.host._owned_action.reset_mock()
        with self.assertRaises(ValueError):
            self.host.add_path('session', 'AhShape', self.points,
                               group_path=[-1], expected_group_name='Ah')
        self.host._owned_action.assert_not_called()

    def test_masks_require_exact_layer_identity_and_named_path(self):
        self.host.apply_mask('session', [0], 'Title', 'Curve')
        self.assertEqual(self.host._owned_action.call_args.args[2]['path_name'], 'Curve')
        with self.assertRaises(ValueError):
            self.host.apply_mask('session', [-1], 'Title', 'Curve')
