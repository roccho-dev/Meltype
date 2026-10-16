# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct Input (#16): keys reach the child one at a time; Ctrl+Space converts only the word it saw echoed."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import meltype_tty as m

class Native:
    def __init__(self): self.calls=[];self.cancelled=0
    def key(self,vk,char,shift=0):
        self.calls.append((vk,char,shift))
        if vk==0x1B: return {'consumed':True,'commits':[],'view':None}
        if vk==0x0D: return {'consumed':True,'commits':[{'deleteBefore':0,'text':'日本語'}],'view':None}
        return {'consumed':True,'commits':[],'view':{'text':'composing'}}
    def commit(self): return {'consumed':True,'commits':[{'deleteBefore':0,'text':'確定'}],'view':None}
    def cancel(self): self.cancelled+=1

def typed(text):
    """Direct Input after the child echoed what was typed (as Readline does)."""
    n=Native();b=m.InputBridge(n,direct=True)
    assert b.feed(text)==text
    b.observe(text)
    return n,b

class KeysReachTheChild(unittest.TestCase):
    def test_each_key_is_passed_as_it_is(self):
        n=Native();b=m.InputBridge(n,direct=True)
        for raw in [b'g',b'i',b't',b' ',b'\t',b'\x12',b'\r',b'\x7f',b'\x1b[A',b'\x1b[97;5u',b'\x1b[27;5;117~']:
            self.assertEqual(b.feed(raw),raw)
        self.assertEqual(n.calls,[])

    def test_ctrl_keys_are_the_childs_without_a_composition(self):
        for raw in [b'\x15',b'\x10',b'\x0f',b'\x09',b'\x1b[117;5u',b'\x1b[105;5u']:
            n,b=typed(b'nihongo')
            self.assertEqual(b.feed(raw),raw)
            self.assertEqual(n.calls,[])

    def test_ctrl_space_with_nothing_remembered_reaches_the_child(self):
        n=Native();b=m.InputBridge(n,direct=True)
        self.assertEqual(b.feed(b'\x00'),b'\x00')
        self.assertEqual(b.feed(b'\x1b[32;5u'),b'\x1b[32;5u')
        self.assertEqual(n.calls,[])

class Conversion(unittest.TestCase):
    def test_backspaces_match_the_word_and_letters_enter_the_core(self):
        n,b=typed(b'hello nihongo')
        self.assertEqual(b.feed(b'\x00'),b'\x7f'*7)
        self.assertEqual([c[0] for c in n.calls],[ord(c) for c in 'NIHONGO'])
        self.assertEqual(n.calls[0],(78,110,0))
        self.assertEqual(b.view,{'text':'composing'})
        self.assertEqual(b.feed(b'\r'),'日本語'.encode())
        self.assertIsNone(b.view)

    def test_kitty_and_modify_other_keys_ctrl_space(self):
        for press,release in [(b'\x1b[32;5u',b'\x1b[32;5:3u'),(b'\x1b[27;5;32~',b'')]:
            n,b=typed(b'nihongo')
            self.assertEqual(b.feed(press),b'\x7f'*7)
            self.assertEqual(b.feed(release),b'')

    def test_the_word_is_the_letters_after_the_last_space_or_symbol(self):
        for text,count in [(b'git commit -m nihongo',7),(b'ra-men',6),(b'cd ~/nihongo',7),(b"kan'i",5),(b'x=Tokyo',5)]:
            n,b=typed(text)
            self.assertEqual(b.feed(b'\x00'),b'\x7f'*count,text)
        for text in [b'nihongo ',b'2026',b'nihongo.']:
            n,b=typed(text)
            self.assertEqual(b.feed(b'\x00'),b'\x00',text)
            self.assertEqual(n.calls,[])

    def test_esc_puts_the_word_back(self):
        n,b=typed(b'nihongo')
        b.feed(b'\x00')
        self.assertEqual(b.feed(b'\x1b'),b'')
        self.assertEqual(b.flush_escape(),b'nihongo')
        self.assertIsNone(b.view)

    def test_lost_capture_puts_the_word_back(self):
        n,b=typed(b'nihongo')
        b.feed(b'\x00')
        b.set_capture(False)
        self.assertEqual(n.cancelled,1)
        self.assertIsNone(b.view)
        self.assertEqual(b.expire(),b'nihongo')  # The run loop writes it on its next turn.
        self.assertEqual(b.expire(),b'')

    def test_after_a_commit_direct_input_resumes(self):
        n,b=typed(b'nihongo')
        b.feed(b'\x00');b.feed(b'\r')
        self.assertEqual(b.feed(b'ls'),b'ls')
        self.assertEqual(len(n.calls),8)

class Forgetting(unittest.TestCase):
    """Every way the line may have changed: Ctrl+Space then erases nothing."""
    def assertForgotten(self,n,b):
        self.assertEqual(b.feed(b'\x00'),b'\x00')
        self.assertEqual(n.calls,[])

    def test_keys_that_forget(self):
        for raw in [b'\t',b'\r',b'\n',b'\x15',b'\x03',b'\x12',b'\x1b[A',b'\x1b[D',b'\x1b[3~',b'\x1bb',
                    b'\x1b[117;5u',b'\x1b[27;5;117~',m.PASTE_BEGIN+b'x'+m.PASTE_END,'あ'.encode()]:
            n,b=typed(b'nihongo')
            b.feed(raw)
            self.assertForgotten(n,b)

    def test_esc_alone_forgets(self):
        n,b=typed(b'nihongo')
        b.feed(b'\x1b');b.flush_escape()
        self.assertForgotten(n,b)

    def test_resize_focus_and_capture_forget(self):
        n,b=typed(b'nihongo');b.forget()  # run() calls it on SIGWINCH
        self.assertForgotten(n,b)
        n,b=typed(b'nihongo');b.feed(b'\x1b[O');b.feed(b'\x1b[I')
        self.assertForgotten(n,b)
        n,b=typed(b'nihongo');b.set_capture(False);b.set_capture(True)
        self.assertForgotten(n,b)

    def test_child_output_other_than_the_echo_forgets(self):
        for output in [b'\r\x1b[K$ nihongo',b'x',b'\x07',b'nihongo']:
            n,b=typed(b'nihongo')
            b.observe(output)
            self.assertForgotten(n,b)

    def test_unechoed_keys_are_not_converted(self):
        n=Native();b=m.InputBridge(n,direct=True)
        b.feed(b'nihongo')
        self.assertForgotten(n,b)
        n=Native();b=m.InputBridge(n,direct=True)
        b.feed(b'nihongo');b.observe(b'nihon')
        self.assertForgotten(n,b)

    def test_echo_may_come_in_pieces(self):
        n=Native();b=m.InputBridge(n,direct=True)
        b.feed(b'nihongo');b.observe(b'nih');b.observe(b'ongo')
        self.assertEqual(b.feed(b'\x00'),b'\x7f'*7)

class BackSpace(unittest.TestCase):
    def test_backspace_takes_back_one_letter(self):
        for echo in m.ERASE_ECHOES:
            n,b=typed(b'nihongoo')
            self.assertEqual(b.feed(b'\x7f'),b'\x7f')
            b.observe(echo)
            self.assertEqual(b.feed(b'\x00'),b'\x7f'*7)

    def test_backspace_before_its_echo_converts_nothing(self):
        n,b=typed(b'nihongoo')
        b.feed(b'\x7f')
        self.assertForgottenAfter(n,b)

    def test_unknown_erase_echo_forgets(self):
        n,b=typed(b'nihongoo')
        b.feed(b'\x7f');b.observe(b'\x08\x1b[1P')
        self.assertForgottenAfter(n,b)

    def assertForgottenAfter(self,n,b):
        self.assertEqual(b.feed(b'\x00'),b'\x00')
        self.assertEqual(n.calls,[])

class TabThenConvert(unittest.TestCase):
    def test_completion_is_not_erased(self):
        n,b=typed(b'cat /etc/hos')
        self.assertEqual(b.feed(b'\t'),b'\t')
        b.observe(b'ts ')                 # Readline completes the file name.
        b.feed(b'nihongo');b.observe(b'nihongo')
        self.assertEqual(b.feed(b'\x00'),b'\x7f'*7)

    def test_late_completion_output_never_widens_the_erase(self):
        n,b=typed(b'cat /etc/hos')
        b.feed(b'\t')
        b.feed(b'n')
        b.observe(b'ts n')                # The completion arrived after the next key.
        b.feed(b'ihongo');b.observe(b'ihongo')
        self.assertEqual(b.feed(b'\x00'),b'\x7f'*6)

class CtrlWhileComposing(unittest.TestCase):
    """PR #14: Ctrl+U/P/O (and Kitty Ctrl+I) switch only while a composition is shown."""
    def test_only_inside_a_conversion(self):
        for raw,vk in [(b'\x15',0x55),(b'\x10',0x50),(b'\x0f',0x4F),(b'\x1b[105;5u',0x49)]:
            n,b=typed(b'nihongo')
            self.assertEqual(b.feed(raw),raw)   # No composition: the child's.
            b.feed(b'ka');b.observe(b'ka')
            b.feed(b'\x00')
            self.assertEqual(b.feed(raw),b'')   # Composing: the Core's switch.
            self.assertEqual(n.calls[-1],(vk,0,2))

class ComposeMode(unittest.TestCase):
    def test_compose_mode_is_unchanged(self):
        n=Native();b=m.InputBridge(n)
        self.assertEqual(b.feed(b'n'),b'')
        self.assertEqual(n.calls,[(78,110,0)])

if __name__=='__main__':unittest.main()
