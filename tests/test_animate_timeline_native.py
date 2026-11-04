"""Opt in with CAVI_ANIMATE_TIMELINE_NATIVE=1; licensed Animate must be running."""
import asyncio
import os
import sys
import tempfile
import unittest
from contextlib import nullcontext
from itertools import pairwise
from pathlib import Path

import numpy as np
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image, ImageDraw


@unittest.skipUnless(os.environ.get('CAVI_ANIMATE_TIMELINE_NATIVE') == '1', 'native Animate opt-in')
class TimelineNativeTests(unittest.TestCase):
    def test_saved_symbols_tweens_and_exported_midframe_motion(self):
        asyncio.run(self.run_native())

    async def run_native(self):
        evidence = os.environ.get('CAVI_NATIVE_EVIDENCE')
        folder_context = (nullcontext(tempfile.mkdtemp(prefix='animate-native-', dir=evidence))
                          if evidence else tempfile.TemporaryDirectory())
        with folder_context as folder:
            root=Path(folder).resolve()
            (root/'data').mkdir()
            image=Image.new('RGBA',(128,128))
            ImageDraw.Draw(image).ellipse((10,10,50,50),fill=(30,180,210,255))
            image.save(root/'orb.png')
            params=StdioServerParameters(command=sys.executable,
                args=['-m','cavi_adobe_mcp.animate_server'],
                env={**os.environ,'CAVI_ADOBE_DATA':str(root/'data'),'CAVI_ADOBE_WORKSPACE':str(root)})
            async with stdio_client(params) as (r,w), ClientSession(r,w,read_timeout_seconds=180) as client:
                await client.initialize()
                async def call(tool,**args):
                    reply=await client.call_tool(tool,args)
                    self.assertFalse(reply.is_error,reply)
                    return reply.structured_content
                created=await call('animate_create_from_png',source='orb.png',destination='motion.fla')
                sid=created['session']
                layer=created['document']['layers'][0]['name']
                edits=[{'action':'symbol','layer':layer,'frame':0,'element':0,'name':'Orb'},
                       {'action':'duration','layer':layer,'count':13},
                       {'action':'keyframe','layer':layer,'frame':12},
                       {'action':'transform','layer':layer,'frame':0,'element':0,'matrix':[1,0,0,1,10,10]},
                       {'action':'transform','layer':layer,'frame':12,'element':0,'matrix':[.8,.2,-.2,.8,150,70]},
                       {'action':'tween','layer':layer,'frame':0,'end':12,'easing':30},
                       {'action':'add_layer','layer':'Accent'},
                       {'action':'duration','layer':'Accent','count':13},
                       {'action':'place_symbol','layer':'Accent','frame':0,'name':'Orb','x':300,'y':40}]
                await call('animate_edit_timeline',session_id=sid,batch={'edits':edits})
                await call('animate_export_frames',session_id=sid,directory='frames')
                frames=[np.array(Image.open(root/f'frames/frame_{f:04}.png')) for f in (1,7,13)]
                for a,b in pairwise(frames):
                    self.assertGreater(np.count_nonzero(np.any(a!=b,axis=2)),100)
                await call('animate_close_copy',session_id=sid)
                reopened=await call('animate_open_copy',source='motion.fla',destination='reopened.fla')
                self.assertIn('Orb',[s['name'] for s in reopened['document']['symbols']])
                main=next(l for l in reopened['document']['layers'] if l['name']==layer)
                self.assertEqual(main['keyframes'][0]['tween'],'motion')
                self.assertEqual(main['keyframes'][-1]['frame'],12)
                await call('animate_close_copy',session_id=reopened['session'])
