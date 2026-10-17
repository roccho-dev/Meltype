# SPDX-License-Identifier: GPL-3.0-or-later
"""#19 passive preview: suggestions for the echoed word, drawn without taking a key or erasing more."""
import inspect
import os
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import meltype_tty as m
from kitty_display import KittyDisplay

FONT=os.environ.get('MELTYPE_TTY_TEST_FONT','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
PROPOSALS=[{'id':'mozc:0','representation':'日本語','meaning':{'kind':'text','value':'日本語'},'evidence':['test']},
           {'id':'mozc:1','representation':'ニホンゴ','meaning':{'kind':'text','value':'ニホンゴ'},'evidence':['test']}]

class Native:
    def __init__(self): self.calls=[]
    def key(self,vk,char,shift=0):
        self.calls.append((vk,char,shift))
        return {'consumed':True,'commits':[],'view':{'text':'composing'}}
    def commit(self): return {'consumed':True,'commits':[],'view':None}

class Source:
    def __init__(self,proposals=PROPOSALS,error=None): self.queries=[];self.proposals=proposals;self.error=error
    def __call__(self,query):
        self.queries.append(query)
        if self.error: raise self.error
        return self.proposals

def preview(source,**kwargs):
    cls=getattr(m,'Preview',None)
    if cls is None: raise AssertionError('meltype_tty.Preview is missing')
    return cls(source,**kwargs)

def typed(text):
    n=Native();b=m.InputBridge(n,direct=True)
    assert b.feed(text)==text
    b.observe(text)
    return n,b

class KeysAreNotTaken(unittest.TestCase):
    def test_every_key_reaches_the_child_unchanged_while_previewing(self):
        n=Native();b=m.InputBridge(n,direct=True);p=preview(Source(),delay=0)
        t=0
        for raw in [b'e',b'c',b'h',b'o',b' ',b'n',b'i',b'\t',b'\x12',b'\x1b[A',b'\x7f',b'\r']:
            self.assertEqual(b.feed(raw),raw)
            if 32<=raw[0]<127: b.observe(raw)
            t+=1;p.update(b,t)
        self.assertEqual(n.calls,[])

    def test_ctrl_space_erases_exactly_the_previewed_word(self):
        n,b=typed(b'echo nihongo');p=preview(Source(),delay=0)
        self.assertIsNotNone(p.update(b,1))
        self.assertEqual(b.feed(b'\x00'),b'\x7f'*7)
        self.assertIsNone(p.update(b,2))

class Throttle(unittest.TestCase):
    def test_one_query_after_the_word_settles(self):
        source=Source();n,b=typed(b'nihongo');p=preview(source,delay=.15)
        self.assertIsNone(p.update(b,10.0))
        self.assertIsNone(p.update(b,10.1))
        view=p.update(b,10.2)
        self.assertEqual(view['candidates'],['日本語','ニホンゴ'])
        self.assertEqual(p.update(b,10.5),view)
        self.assertEqual(len(source.queries),1)
        self.assertEqual(set(source.queries[0]),{'input','state','focus'})
        self.assertEqual(source.queries[0]['input'],'nihongo')

    def test_each_key_restarts_the_wait(self):
        source=Source();n=Native();b=m.InputBridge(n,direct=True);p=preview(source,delay=.15)
        t=0.0
        for ch in b'nihongo':
            b.feed(bytes([ch]));b.observe(bytes([ch]))
            self.assertIsNone(p.update(b,t));t+=.05
        p.update(b,t+.2)
        self.assertEqual([q['input'] for q in source.queries],['nihongo'])

class WhatIsPreviewed(unittest.TestCase):
    def test_nothing_before_the_echo_or_after_the_line_changes(self):
        source=Source();n=Native();b=m.InputBridge(n,direct=True);p=preview(source,delay=0)
        b.feed(b'nihongo')
        self.assertIsNone(p.update(b,1))
        b.observe(b'nihongo')
        self.assertIsNotNone(p.update(b,2))
        b.feed(b'\t')
        self.assertIsNone(p.update(b,3))
        self.assertEqual(len(source.queries),1)

    def test_semcmp_shaped_proposals_keep_their_identity(self):
        bad=[{'id':'x','meaning':{}},{'id':'y','representation':''}]
        n,b=typed(b'nihongo');p=preview(Source(PROPOSALS+bad),delay=0)
        view=p.update(b,1)
        self.assertEqual([x['id'] for x in view['proposals']],['mozc:0','mozc:1'])
        self.assertIn('Ctrl+Space',view['hint'])

    def test_a_failing_source_shows_nothing(self):
        n,b=typed(b'nihongo');p=preview(Source(error=RuntimeError('down')),delay=0)
        self.assertIsNone(p.update(b,1))
        self.assertIsNone(p.update(b,2))

    def test_no_proposals_shows_nothing(self):
        n,b=typed(b'git');p=preview(Source([]),delay=0)
        self.assertIsNone(p.update(b,1))

    def test_off_by_default(self):
        self.assertIs(inspect.signature(m.run).parameters['preview'].default,False)
        self.assertIn('--preview',inspect.getsource(m.main))

class Placement(unittest.TestCase):
    VIEW={'text':'nihongo','candidates':['日本語','ニホンゴ'],'selectedIndex':-1,'hint':'Ctrl+Space: convert'}
    def display(self): return KittyDisplay(FONT,24,80,4242)

    def test_drawn_only_above_the_cursor_line(self):
        d=self.display();d.cursor=(20,5)
        out=d.update(self.VIEW,passive=True)
        top=int(re.match(rb'\x1b\[([0-9]+);[0-9]+H',out)[1])
        rows=int(re.search(rb',r=([0-9]+),',out)[1])
        self.assertLessEqual(top+rows,20)

    def test_no_room_above_means_nothing_drawn(self):
        d=self.display();d.cursor=(2,5)
        self.assertNotIn(b'a=T',d.update(self.VIEW,passive=True))

    def test_removed_before_any_child_output(self):
        d=self.display();d.cursor=(20,5)
        d.update(self.VIEW,passive=True)
        self.assertTrue(d.output(b'completions\r\n').startswith(b'\x1b_Ga=d,d=I,i=4242'))

    def test_a_late_cursor_never_stops_typing(self):
        d=self.display()
        with patch('kitty_display.time.monotonic',return_value=10): self.assertEqual(d.update(self.VIEW,passive=True),b'\x1b[6n')
        with patch('kitty_display.time.monotonic',return_value=11.6): self.assertEqual(d.update(self.VIEW,passive=True),b'')
        self.assertFalse(d.pending)
        self.assertEqual(d.update(self.VIEW,passive=True),b'')

    def test_origin_mode_skips_the_preview(self):
        d=self.display();d.output(b'\x1b[?6h')
        self.assertEqual(d.update(self.VIEW,passive=True),b'')

if __name__=='__main__':unittest.main()
