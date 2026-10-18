# SPDX-License-Identifier: GPL-3.0-or-later
"""#19 passive preview: suggestions for the echoed word, drawn without taking a key or erasing more."""
import inspect
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import meltype_tty as m
from kitty_display import KittyDisplay

FONT=os.environ.get('MELTYPE_TTY_TEST_FONT','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
PROPOSALS=[{'id':'mozc:0','representation':'日本語','meaning':{'kind':'text','value':'日本語'},'evidence':['test']},
           {'id':'mozc:1','representation':'ニホンゴ','meaning':{'kind':'text','value':'ニホンゴ'},'evidence':['test']}]
# The Core's view always carries its own (Windows) key hint; only a preview may draw a hint.
CORE_VIEW={'text':'日本語','converting':True,'selectedIndex':1,'selectedClause':0,'hint':'F7: カタカナ  F10: 英字',
           'candidates':['日本語','ニホンゴ','にほんご'],'clauses':[],'suggestion':None}
DEFAULT_HINT='↑↓ / Space   Enter: commit   Esc: cancel'

class Native:
    def __init__(self): self.calls=[]
    def key(self,vk,char,shift=0):
        self.calls.append((vk,char,shift))
        return {'consumed':True,'commits':[],'view':{'text':'composing'}}
    def commit(self): return {'consumed':True,'commits':[],'view':None}

class Source:
    def __init__(self,proposals=PROPOSALS,error=None,sleep=0): self.queries=[];self.proposals=proposals;self.error=error;self.sleep=sleep
    def __call__(self,query):
        self.queries.append(query)
        if self.sleep: time.sleep(self.sleep)
        if self.error: raise self.error
        return self.proposals

def preview(source,**kwargs):
    cls=getattr(m,'Preview',None)
    if cls is None: raise AssertionError('meltype_tty.Preview is missing')
    return cls(source,**kwargs)

def ask(p,b,now):
    """Update, wait for a query the update started (if the source runs off the input path), update again."""
    view=p.update(b,now)
    thread=getattr(p,'thread',None)
    if thread is not None: thread.join(5)
    return p.update(b,now)

def typed(text):
    n=Native();b=m.InputBridge(n,direct=True)
    assert b.feed(text)==text
    b.observe(text)
    return n,b

def drawn_lines(display,view,**kwargs):
    lines=[]
    class Draw:
        def text(self,position,line,**_):lines.append(line)
    with patch('PIL.ImageDraw.Draw',return_value=Draw()):display.paint(view,**kwargs)
    return lines

class KeysAreNotTaken(unittest.TestCase):
    def test_every_key_reaches_the_child_unchanged_while_previewing(self):
        n=Native();b=m.InputBridge(n,direct=True);p=preview(Source(),delay=0)
        t=0
        for raw in [b'e',b'c',b'h',b'o',b' ',b'n',b'i',b'\t',b'\x12',b'\x1b[A',b'\x7f',b'\r']:
            self.assertEqual(b.feed(raw),raw)
            if 32<=raw[0]<127: b.observe(raw)
            t+=1;ask(p,b,t)
        self.assertEqual(n.calls,[])

    def test_ctrl_space_erases_exactly_the_previewed_word(self):
        n,b=typed(b'echo nihongo');p=preview(Source(),delay=0)
        self.assertIsNotNone(ask(p,b,1))
        self.assertEqual(b.feed(b'\x00'),b'\x7f'*7)
        self.assertIsNone(p.update(b,2))

class Latency(unittest.TestCase):
    """The input path never waits for the source: the bound is one update(), not one query."""
    def test_a_slow_source_never_holds_an_update(self):
        n,b=typed(b'nihongo');p=preview(Source(sleep=1.0),delay=0)
        started=time.monotonic()
        p.update(b,1)
        self.assertLess(time.monotonic()-started,.05)
        self.assertEqual(b.feed(b'x'),b'x')
        self.assertLess(time.monotonic()-started,.05)

    def test_an_answer_later_than_the_timeout_is_not_shown(self):
        n,b=typed(b'nihongo');p=preview(Source(sleep=.3),delay=0,timeout=.1)
        self.assertIsNone(ask(p,b,1))

    def test_an_answer_for_an_older_word_is_not_shown(self):
        n,b=typed(b'nihongo');source=Source(sleep=.2);p=preview(source,delay=0)
        p.update(b,1)
        b.feed(b'a');b.observe(b'a')
        p.thread.join(5)
        self.assertIsNone(p.update(b,1.01))
        self.assertEqual(ask(p,b,2)['text'],'nihongoa')

    def test_one_query_at_a_time(self):
        live=[];peak=[]
        lock=threading.Lock()
        def slow(query):
            with lock: live.append(1);peak.append(len(live))
            time.sleep(.1)
            with lock: live.pop()
            return PROPOSALS
        n=Native();b=m.InputBridge(n,direct=True);p=preview(slow,delay=0)
        for i,ch in enumerate(b'nihongo'):
            b.feed(bytes([ch]));b.observe(bytes([ch]));p.update(b,i)
        p.thread.join(5)
        self.assertEqual(max(peak),1)

    def test_words_longer_than_the_limit_are_not_asked(self):
        source=Source();n,b=typed(b'a'*33);p=preview(source,delay=0)
        self.assertIsNone(ask(p,b,1))
        self.assertEqual(source.queries,[])

class Throttle(unittest.TestCase):
    def test_one_query_after_the_word_settles(self):
        source=Source();n,b=typed(b'nihongo');p=preview(source,delay=.15)
        self.assertIsNone(ask(p,b,10.0))
        self.assertIsNone(ask(p,b,10.1))
        view=ask(p,b,10.2)
        self.assertEqual(view['candidates'],['日本語','ニホンゴ'])
        self.assertEqual(ask(p,b,10.5),view)
        self.assertEqual(len(source.queries),1)
        self.assertEqual(set(source.queries[0]),{'input','state','focus'})
        self.assertEqual(source.queries[0]['input'],'nihongo')

    def test_each_key_restarts_the_wait(self):
        source=Source();n=Native();b=m.InputBridge(n,direct=True);p=preview(source,delay=.15)
        t=0.0
        for ch in b'nihongo':
            b.feed(bytes([ch]));b.observe(bytes([ch]))
            self.assertIsNone(ask(p,b,t));t+=.05
        ask(p,b,t+.2)
        self.assertEqual([q['input'] for q in source.queries],['nihongo'])

class WhatIsPreviewed(unittest.TestCase):
    def test_nothing_before_the_echo_or_after_the_line_changes(self):
        source=Source();n=Native();b=m.InputBridge(n,direct=True);p=preview(source,delay=0)
        b.feed(b'nihongo')
        self.assertIsNone(ask(p,b,1))
        b.observe(b'nihongo')
        self.assertIsNotNone(ask(p,b,2))
        b.feed(b'\t')
        self.assertIsNone(ask(p,b,3))
        self.assertEqual(len(source.queries),1)

    def test_semcmp_shaped_proposals_keep_their_identity(self):
        bad=[{'id':'x','meaning':{}},{'id':'y','representation':''}]
        n,b=typed(b'nihongo');p=preview(Source(PROPOSALS+bad),delay=0)
        view=ask(p,b,1)
        self.assertEqual([x['id'] for x in view['proposals']],['mozc:0','mozc:1'])
        self.assertIn('Ctrl+Space',view['hint'])

    def test_a_failing_source_shows_nothing(self):
        n,b=typed(b'nihongo');p=preview(Source(error=RuntimeError('down')),delay=0)
        self.assertIsNone(ask(p,b,1))
        self.assertIsNone(ask(p,b,2))

    def test_no_proposals_shows_nothing(self):
        n,b=typed(b'git');p=preview(Source([]),delay=0)
        self.assertIsNone(ask(p,b,1))

    def test_off_by_default(self):
        self.assertIs(inspect.signature(m.run).parameters['preview'].default,False)
        self.assertIn('--preview',inspect.getsource(m.main))

class OffIsUnchanged(unittest.TestCase):
    """Without --preview, the candidate image is v1.2.1.5's: the Core's hint is never drawn."""
    def display(self):
        d=KittyDisplay(FONT,24,80,4242);d.cursor=(22,5);return d

    def test_composition_image_draws_the_v1215_hint_not_the_cores(self):
        lines=drawn_lines(self.display(),CORE_VIEW)
        self.assertEqual(lines,['日本語','  日本語','▶ ニホンゴ','  にほんご',DEFAULT_HINT])

    def test_composition_bytes_do_not_depend_on_the_core_hint(self):
        without={k:v for k,v in CORE_VIEW.items() if k!='hint'}
        self.assertEqual(self.display().update(CORE_VIEW),self.display().update(without))

    def test_only_a_preview_draws_its_hint(self):
        view={'text':'nihongo','candidates':['日本語'],'selectedIndex':-1,'hint':'Ctrl+Space: convert'}
        self.assertEqual(drawn_lines(self.display(),view,passive=True)[-1],'Ctrl+Space: convert')

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

    def test_the_late_reply_is_not_passed_to_the_child(self):
        d=self.display()
        with patch('kitty_display.time.monotonic',return_value=10): d.update(self.VIEW,passive=True)
        with patch('kitty_display.time.monotonic',return_value=11.6): d.update(self.VIEW,passive=True)
        self.assertTrue(d.reply(b'\x1b[3;4R'))
        self.assertIn('later',d.preview_off)

    def test_a_cursor_outside_the_screen_turns_previews_off(self):
        d=self.display()
        self.assertEqual(d.update(self.VIEW,passive=True),b'\x1b[6n')
        self.assertTrue(d.reply(b'\x1b[999;1R'))
        self.assertFalse(d.pending)
        self.assertIn('outside',d.preview_off)
        self.assertEqual(d.update(self.VIEW,passive=True),b'')

    def test_a_composition_cursor_outside_the_screen_still_fails(self):
        d=self.display();d.update(CORE_VIEW)
        with self.assertRaises(ValueError): d.reply(b'\x1b[999;1R')

    def test_origin_mode_skips_the_preview(self):
        d=self.display();d.output(b'\x1b[?6h')
        self.assertEqual(d.update(self.VIEW,passive=True),b'')

class Observation(unittest.TestCase):
    """--preview-fd: an explicit append-only file of what was shown and hidden; nothing by default."""
    def test_show_and_hide_events_carry_content_and_rectangle(self):
        cls=getattr(m,'PreviewLog',None)
        if cls is None: raise AssertionError('meltype_tty.PreviewLog is missing')
        n,b=typed(b'nihongo');p=preview(Source(),delay=0);view=ask(p,b,1)
        d=KittyDisplay(FONT,24,80,4242);d.cursor=(20,5)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'preview.jsonl'
            fd=os.open(path,os.O_CREAT|os.O_WRONLY|os.O_APPEND,0o600)
            try:
                log=cls(fd)
                d.update(view,passive=True);log.note(d,p,'update')
                d.output(b'listing\r\n');log.note(d,p,'child output')
            finally: os.close(fd)
            events=[json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual([e['event'] for e in events],['show','hide'])
        self.assertEqual(events[0]['input'],'nihongo')
        self.assertEqual(events[0]['representations'],['日本語','ニホンゴ'])
        top,left,rows,cols=events[0]['rect']
        self.assertLessEqual(top+rows,events[0]['cursor'][0])
        self.assertEqual(events[1]['reason'],'child output')

    def test_off_by_default(self):
        self.assertIsNone(inspect.signature(m.run).parameters['preview_fd'].default)

    def test_stdio_pipe_and_readonly_are_rejected(self):
        for fd in [0,1,2,-1]:
            with self.subTest(fd=fd),self.assertRaises(ValueError): m.validate_observation_fd(fd)
        r,w=os.pipe()
        try:
            with self.assertRaises(ValueError): m.validate_observation_fd(w)
        finally: os.close(r);os.close(w)
        with tempfile.TemporaryFile() as file:
            with self.assertRaises(ValueError): m.validate_observation_fd(file.fileno())
            readonly=os.open('/dev/null',os.O_RDONLY)
            try:
                with self.assertRaises(ValueError): m.validate_observation_fd(readonly)
            finally: os.close(readonly)

    def test_invalid_observer_never_starts_child(self):
        from unittest.mock import Mock
        with patch.object(m.os,'isatty',return_value=True),patch.object(m.pty,'fork') as fork:
            with self.assertRaises(ValueError): m.run(['/bin/true'],Mock(),preview_fd=1)
            fork.assert_not_called()

class Persistence(unittest.TestCase):
    """While the child only echoes the typed keys, the preview stays and is replaced in place."""
    ANSWERS={'nihong':[{'id':'a','representation':'にほんg','meaning':{},'evidence':[]}],
             'nihongo':[{'id':'b','representation':'日本語','meaning':{},'evidence':[]}]}

    def setUp(self):
        self.source=lambda q:self.ANSWERS.get(q['input'],[])
        self.n,self.b=typed(b'echo nihong')
        self.p=preview(self.source,delay=0)
        self.d=KittyDisplay(FONT,24,80,4242);self.d.cursor=(20,12)
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'preview.jsonl'
        self.fd=os.open(self.path,os.O_CREAT|os.O_WRONLY|os.O_APPEND,0o600);self.addCleanup(os.close,self.fd)
        self.log=m.PreviewLog(self.fd)

    def events(self): return [json.loads(line) for line in self.path.read_text().splitlines()]

    def show(self,now):
        out=self.d.update(ask(self.p,self.b,now),passive=True);self.log.note(self.d,self.p,'update');return out

    def type_echoed(self,key):
        self.assertEqual(self.b.feed(key),key)
        moved=self.b.observe(key)
        out=self.d.output(key,moved);self.log.note(self.d,self.p,'child output')
        return out

    def refresh(self,now):
        self.d.update(self.p.update(self.b,now),passive=True);self.log.note(self.d,self.p,'update')

    def test_a_refresh_between_a_key_and_its_echo_keeps_the_preview(self):
        # run() refreshes right after sending each key, before the child echoes it (bcon VM, run 39022446603:
        # 'h' was hidden with reason "update" and redrawn one column to the right).
        self.show(1)
        t=2
        for key,echo in [(b'o',b'o'),(b'\x7f',b'\x08\x1b[K')]:
            self.assertEqual(self.b.feed(key),key)
            self.refresh(t);t+=1                       # the key is out, its echo not back yet
            self.assertTrue(self.d.visible,key)
            self.d.output(echo,self.b.observe(echo));self.log.note(self.d,self.p,'child output')
            self.refresh(t);t+=1
            if self.p.thread is not None: self.p.thread.join(5)
            self.refresh(t);t+=1
        events=self.events()
        self.assertEqual([e for e in events if e['event']=='hide'],[])
        self.assertEqual(len({e['rect'][1] for e in events}),1)
        self.assertEqual((events[-1]['input'],events[-1]['stale']),('nihong',False))

    def test_observe_reports_how_far_an_echo_moved_the_cursor(self):
        n=Native();b=m.InputBridge(n,direct=True)
        b.feed(b'ab');self.assertEqual(b.observe(b'ab'),2)
        b.feed(b'\x7f');self.assertEqual(b.observe(b'\x08\x1b[K'),-1)
        self.assertIsNone(b.observe(b'\r\nlisting'))

    def test_an_echo_keeps_the_preview_and_it_is_replaced_in_place(self):
        self.assertIn(b'a=T',self.show(1))
        self.assertEqual(self.type_echoed(b'o'),b'o')            # no delete before the echo
        stale=self.d.update(self.p.update(self.b,2),passive=True);self.log.note(self.d,self.p,'update')
        fresh=self.show(3)
        for out in [stale,fresh]:
            self.assertIn(b'a=T',out)
            self.assertNotIn(b'a=d',out)                          # replaced under the same id, never removed
            self.assertIn(b'i=4242',out)
        events=self.events()
        self.assertEqual([e['event'] for e in events],['show','update','update'])
        self.assertEqual([(e['input'],e['stale']) for e in events],[('nihong',False),('nihong',True),('nihongo',False)])
        self.assertEqual(events[-1]['representations'],['日本語'])
        self.assertEqual(len({e['rect'][1] for e in events}),1)   # the same place

    def test_stale_candidates_are_marked_and_never_shown_as_current(self):
        self.show(1)
        self.type_echoed(b'o')
        view=self.p.update(self.b,2)
        self.assertTrue(view['stale'])
        self.assertEqual(view['text'],'nihong')                   # labelled with the word they belong to
        fills=[];lines=[]
        class Draw:
            def text(self,position,line,fill=None,**_):lines.append(line);fills.append(fill)
        with patch('PIL.ImageDraw.Draw',return_value=Draw()):self.d.paint(view,passive=True)
        self.assertTrue(lines[0].endswith('…'))
        fresh=[];
        class Fresh:
            def text(self,position,line,fill=None,**_):fresh.append(fill)
        with patch('PIL.ImageDraw.Draw',return_value=Fresh()):self.d.paint(dict(view,stale=False),passive=True)
        self.assertTrue(all(a!=b for a,b in zip(fills,fresh)))   # dimmed

    def test_output_other_than_the_echo_hides(self):
        self.show(1)
        for output in [b'\r\nnihongo-a  nihongo-b\r\n',b'\x1b[2J',b'\x1b[1A']:
            self.setUp();self.show(1)
            self.assertIsNone(self.b.observe(output))
            self.assertTrue(self.d.output(output,None).startswith(b'\x1b_Ga=d,d=I,i=4242'))
            self.log.note(self.d,self.p,'child output')
            self.assertEqual(self.events()[-1],{'event':'hide','reason':'child output'})

    def test_an_echo_past_the_last_column_hides(self):
        self.d.cursor=(20,80);self.show(1)
        self.assertTrue(self.d.output(b'o',1).startswith(b'\x1b_Ga=d'))

    def test_no_answer_for_the_new_word_hides_the_stale_one(self):
        self.show(1)
        self.type_echoed(b'x')
        self.assertTrue(self.p.update(self.b,2)['stale'])
        self.assertIsNone(ask(self.p,self.b,3))

    def test_the_word_gone_or_a_conversion_hides(self):
        self.show(1)
        self.b.feed(b'\t')
        self.assertIsNone(self.p.update(self.b,2))
        self.setUp();self.show(1)
        self.b.feed(b'\x00')
        self.assertIsNotNone(self.b.view)
        self.assertIsNone(self.p.update(self.b,2))

if __name__=='__main__':unittest.main()
