# SPDX-License-Identifier: GPL-3.0-or-later
"""Adversarial byte/ownership cases, not a real terminal or NativeAOT proof.

Image requests and replies below are public synthetic protocol bytes.  These
cases test the existing input/cancel/privacy contract without changing its
timers, key meanings, or the terminal producer.  The product author must also
review these independently authored tests; a pass is not whole-B acceptance.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import meltype_tty as m
from kitty_display import Boundary, KittyDisplay


def image(image_id, more=0, quiet=0):
    return f'\x1b_Ga=T,i={image_id},q={quiet},m={more};AAAA\x1b\\'.encode()


def ack(image_id, result='OK'):
    return f'\x1b_Gi={image_id};{result}\x1b\\'.encode()


class NativeProbe:
    def __init__(self):
        self.target = 'synthetic-pty:1:child:7'
        self.keys = []
        self.commits = 0
        self.focuses = []
        self.cancels = 0

    def key(self, vk, char, modifiers=0):
        self.keys.append((vk, char, modifiers))
        return {'consumed': False, 'commits': [], 'view': None}

    def commit(self):
        self.commits += 1
        return {'consumed': True, 'commits': [{'deleteBefore': 0, 'text': '日本語'}], 'view': None}

    def focus(self, active):
        self.focuses.append(active)

    def cancel(self):
        self.cancels += 1


class ImageReplyOwnershipTests(unittest.TestCase):
    def bridge(self, *ids, capture=True):
        native = NativeProbe()
        boundary = Boundary()
        for image_id in ids:
            boundary.feed(image(image_id))
        bridge = m.InputBridge(native)
        bridge.set_capture(capture)
        bridge.image_replies = lambda: frozenset(serial for serial, _ in boundary.graphics_replies)

        def reply(raw):
            boundary.reply(raw)
            return False  # A child-owned reply must reach that same child.

        bridge.on_report = reply
        native.focuses.clear()
        return bridge, native, boundary

    def at(self, now, method, *args, **kwargs):
        with patch.object(m.time, 'monotonic', return_value=now):
            return method(*args, **kwargs)

    def held_escape(self, bridge):
        self.assertEqual(self.at(10, bridge.feed, b'\x1b'), b'')
        self.assertEqual(self.at(10.07, bridge.expire, continuation=b'z'), b'')

    def release_all(self, bridge, now=10.2):
        result = bytearray()
        for _ in range(4097):
            if not self.at(now, bridge.held_ready):
                return bytes(result)
            result.extend(self.at(now, bridge.release_input))
        self.fail('semantic events were replayed instead of drained once')

    def test_ack_split_at_every_byte_survives_empty_ready_cycles_without_native_input(self):
        response = ack(17)
        for split in range(1, len(response)):
            with self.subTest(split=split):
                bridge, native, boundary = self.bridge(17)
                bridge.view = {'text': 'unchanged preedit'}
                before = self.at(10, bridge.feed, response[:split])
                idle = self.at(10.07, bridge.expire)
                after = self.at(10.1, bridge.feed, response[split:])
                self.assertEqual(before + idle + after, response)
                self.assertEqual(native.keys, [])
                self.assertEqual(native.commits, 0)
                self.assertEqual(bridge.view, {'text': 'unchanged preedit'})
                self.assertEqual(boundary.graphics_replies, [])

    def test_bare_escape_and_later_text_keep_semantic_order_after_exact_ack(self):
        bridge, native, _ = self.bridge(17)
        bridge.view = {'text': 'must cancel, never commit'}
        self.held_escape(bridge)
        self.assertEqual(self.at(10.08, bridge.feed, b'z'), b'')
        self.assertEqual(native.keys, [])
        self.assertEqual(self.at(10.1, bridge.feed, ack(17)), ack(17))
        self.assertEqual(native.keys, [])
        self.assertEqual(self.release_all(bridge), b'\x1bz')
        self.assertEqual(native.keys, [(27, 0, 0), (90, 122, 0)])
        self.assertEqual(native.commits, 0)
        self.assertFalse(bridge.held)

    def test_pending_images_do_not_change_the_unprotected_escape_deadline(self):
        bridge, native, _ = self.bridge()
        self.at(10, bridge.feed, b'\x1b')
        self.assertEqual(self.at(10.061, bridge.expire), b'\x1b')
        self.assertEqual(native.keys, [(27, 0, 0)])

    def test_partial_write_then_user_escape_and_new_ack_cannot_invent_cancel(self):
        bridge, native, boundary = self.bridge(17)
        self.at(10, bridge.feed, b'\x1b')  # Only a failed writer's prefix arrived.
        self.assertEqual(self.at(10.1, bridge.feed, b'\x1b'), b'')
        boundary.feed(image(18))
        self.assertEqual(self.at(10.2, bridge.feed, ack(18)), ack(18))
        self.assertEqual(native.keys, [])
        self.assertFalse(self.at(10.3, bridge.held_ready))
        with self.assertRaises(m.UnsafeEffect):
            self.at(11.5, bridge.expire)
        self.assertEqual(native.keys, [])
        self.assertTrue(any(image_id == 17 for _, image_id in boundary.graphics_replies))

    def test_unknown_or_duplicate_ack_does_not_discharge_another_image(self):
        bridge, native, boundary = self.bridge(17, 18)
        self.held_escape(bridge)
        for now, response in [(10.1, ack(99)), (10.2, ack(17)), (10.3, ack(17))]:
            self.assertEqual(self.at(now, bridge.feed, response), response)
            self.assertFalse(self.at(now, bridge.held_ready))
            self.assertEqual(native.keys, [])
        self.assertEqual([image_id for _, image_id in boundary.graphics_replies], [18])
        self.assertEqual(self.at(10.4, bridge.feed, ack(18)), ack(18))
        self.assertEqual(self.release_all(bridge, 10.5), b'\x1b')
        self.assertEqual(native.keys, [(27, 0, 0)])

    def test_malformed_or_incomplete_ack_does_not_discharge_ownership(self):
        for raw in [b'\x1b_Gi=17;OK', b'\x1b_Gi=17;OK\x1b', b'\x1b_Gi=17x;OK\x1b\\',
                    b'\x1b_Gi=17;OK\x1b\\junk']:
            with self.subTest(raw=raw):
                boundary = Boundary()
                boundary.feed(image(17))
                self.assertFalse(boundary.reply(raw))
                self.assertEqual(len(boundary.graphics_replies), 1)

    def test_complete_error_is_a_reply_not_a_render_success(self):
        bridge, native, boundary = self.bridge(17)
        response = ack(17, 'EINVAL')
        self.assertEqual(self.at(10, bridge.feed, response), response)
        self.assertEqual(boundary.graphics_replies, [])
        self.assertEqual(native.keys, [])

    def test_snapshot_waits_for_all_original_ids_but_not_newer_ids(self):
        bridge, native, boundary = self.bridge(17, 18)
        self.held_escape(bridge)
        original = bridge.reply_tokens
        boundary.feed(image(19))  # Ownership-only test; run separately checks child/view changes.
        self.at(10.1, bridge.feed, ack(17))
        self.assertEqual(bridge.reply_tokens, original)
        self.assertFalse(self.at(10.2, bridge.held_ready))
        self.at(10.3, bridge.feed, ack(18))
        self.assertTrue(self.at(10.4, bridge.held_ready))
        self.assertEqual(self.release_all(bridge, 10.4), b'\x1b')
        self.assertEqual(native.keys, [(27, 0, 0)])
        self.assertEqual([image_id for _, image_id in boundary.graphics_replies], [19])

    def test_paused_held_key_does_not_turn_a_new_split_ack_into_more_keys(self):
        bridge, native, boundary = self.bridge(17)
        self.held_escape(bridge)
        self.at(10.1, bridge.feed, ack(17))
        bridge.paused = True  # The key is still waiting for the ordinary cursor boundary.
        boundary.feed(image(18))
        response = ack(18)
        self.assertEqual(self.at(10.2, bridge.feed, response[:1]), b'')
        self.assertEqual(self.at(10.27, bridge.expire), b'')
        self.assertEqual(self.at(10.3, bridge.feed, response[1:]), response)
        self.assertEqual(boundary.graphics_replies, [])
        self.assertEqual(native.keys, [])
        bridge.paused = False
        self.assertEqual(self.release_all(bridge, 10.4), b'\x1b')
        self.assertEqual(native.keys, [(27, 0, 0)], 'the new ACK must not manufacture another Escape or text key')

    def test_new_ack_while_held_never_resets_the_first_absolute_deadline(self):
        bridge, native, boundary = self.bridge(17)
        self.held_escape(bridge)
        self.at(10.1, bridge.feed, ack(17))
        bridge.paused = True
        boundary.feed(image(18))
        self.at(10.9, bridge.feed, ack(18)[:1])
        with self.assertRaises(m.UnsafeEffect):
            self.at(11.5, bridge.expire)
        self.assertEqual(native.keys, [])

    def test_additional_keys_and_partial_acks_never_extend_the_absolute_deadline(self):
        bridge, native, boundary = self.bridge(17, 18)
        self.held_escape(bridge)
        self.at(10.5, bridge.feed, b'abc')
        self.at(10.9, bridge.feed, ack(17))
        boundary.feed(image(19))
        self.at(11.49, bridge.feed, b'q')
        with self.assertRaises(m.UnsafeEffect):
            self.at(11.5, bridge.feed, ack(18))
        self.assertEqual(native.keys, [])

    def test_view_or_child_identity_change_prevents_held_effects(self):
        for change in ['view', 'target', 'source']:
            with self.subTest(change=change):
                bridge, native, _ = self.bridge(17)
                self.held_escape(bridge)
                self.at(10.1, bridge.feed, ack(17))
                if change == 'view':
                    bridge.view = {'text': 'new provider generation', 'generation': 2}
                elif change == 'target':
                    native.target = 'synthetic-pty:2:child:8'
                else:
                    bridge.source = b'different original word'
                with self.assertRaises(m.UnsafeEffect):
                    self.at(10.2, bridge.release_input)
                self.assertEqual(native.keys, [])

    def test_capture_focus_resize_and_provider_invalidation_precede_any_native_effect(self):
        for event in ['capture', 'focus', 'resize', 'provider']:
            with self.subTest(event=event):
                bridge, native, _ = self.bridge(17)
                self.held_escape(bridge)
                with self.assertRaises(m.UnsafeEffect):
                    if event == 'capture':
                        bridge.set_capture(False)
                    elif event == 'focus':
                        bridge.set_focus(False)
                    else:
                        bridge.invalidate_input()
                self.assertEqual(native.keys, [])
                self.assertEqual(native.focuses, [])
                self.assertEqual(native.cancels, 0)

    def test_change_between_released_events_cannot_select_a_new_view(self):
        bridge, native, _ = self.bridge(17)
        self.held_escape(bridge)
        self.at(10.08, bridge.feed, b'z')
        self.at(10.1, bridge.feed, ack(17))
        self.assertEqual(self.at(10.2, bridge.release_input), b'\x1b')
        bridge.view = {'text': 'new response after refresh'}
        with self.assertRaises(m.UnsafeEffect):
            self.at(10.21, bridge.release_input)
        self.assertEqual(native.keys, [(27, 0, 0)])

    def test_private_split_reply_is_byte_exact_native_free_and_not_left_pending(self):
        bridge, native, boundary = self.bridge(17, capture=False)
        response = ack(17)
        output = self.at(10, bridge.feed, response[:1])
        output += self.at(10.07, bridge.expire)
        output += self.at(10.1, bridge.feed, response[1:])
        self.assertEqual(output, response)
        self.assertEqual(native.keys, [])
        self.assertEqual(native.commits, 0)
        self.assertEqual(boundary.graphics_replies, [], 'already forwarded ACK must not poison the next public session')
        bridge.set_capture(True)
        self.at(10.2, bridge.feed, b'\x1b')
        self.assertEqual(self.at(10.261, bridge.expire), b'\x1b')
        self.assertEqual(native.keys, [(27, 0, 0)])

    def test_private_held_key_contents_and_key_order_survive_without_native_calls(self):
        bridge, native, _ = self.bridge(17, capture=False)
        self.held_escape(bridge)
        keys = b'synthetic-private\x1b[A'
        self.assertEqual(self.at(10.08, bridge.feed, keys), b'')
        self.assertEqual(self.at(10.1, bridge.feed, ack(17)), ack(17))
        self.assertEqual(self.release_all(bridge), b'\x1b' + keys)
        self.assertEqual(native.keys, [])
        self.assertEqual(native.commits, 0)
        # This asserts user-key content/order, not all stdin bytes' relative
        # order: the acknowledged control reply can precede held private keys.

    def test_complete_modified_key_is_one_semantic_event_not_reparsed_bytes(self):
        bridge, native, _ = self.bridge(17)
        bridge.view = {'text': 'existing composition'}
        key = b'\x1b[1;2A'  # Shift+Up, a complete key sequence.
        self.assertEqual(self.at(10, bridge.feed, key), b'')
        self.assertEqual(self.at(10.08, bridge.feed, b'q'), b'')
        self.assertEqual(native.keys, [])
        self.at(10.1, bridge.feed, ack(17))
        self.assertEqual(self.release_all(bridge), key + b'q')
        self.assertEqual(native.keys, [(0x26, 0, 1), (81, 113, 0)])
        self.assertEqual(native.commits, 0)

    def test_paste_and_following_enter_are_released_once_in_original_order(self):
        bridge, native, _ = self.bridge(17)
        self.held_escape(bridge)
        paste = m.PASTE_BEGIN + '合成\nsecond\tline'.encode() + m.PASTE_END
        self.assertEqual(self.at(10.08, bridge.feed, paste + b'\r'), b'')
        self.assertEqual(native.keys, [])
        self.at(10.1, bridge.feed, ack(17))
        self.assertEqual(self.release_all(bridge), b'\x1b' + paste + b'\r')
        self.assertEqual(native.keys, [(27, 0, 0), (13, 0, 0)])
        self.assertEqual(native.commits, 0)
        self.assertEqual(self.release_all(bridge), b'')

    def test_held_event_count_is_bounded_before_native_effects(self):
        bridge, native, _ = self.bridge(17)
        self.held_escape(bridge)
        with self.assertRaises(m.UnsafeEffect):
            self.at(10.1, bridge.feed, b'a' * 4096)
        self.assertEqual(native.keys, [])
        self.assertLessEqual(len(bridge.held), 4096)

    def test_discard_drops_private_raw_and_semantic_events_without_replay(self):
        bridge, native, _ = self.bridge(17)
        self.held_escape(bridge)
        self.at(10.1, bridge.feed, b'synthetic-private')
        bridge.discard_input()
        self.assertFalse(bridge.held)
        self.assertEqual(bridge.held_bytes, 0)
        self.assertIsNone(bridge.reply_tokens)
        self.assertIsNone(bridge.held_binding)
        self.assertEqual(self.release_all(bridge), b'')
        self.assertEqual(native.keys, [])

    def test_chunked_image_registers_one_obligation_and_quiet_overlay_registers_none(self):
        boundary = Boundary()
        command = image(17, more=1) + b'\x1b_Gm=1;BBBB\x1b\\\x1b_Gm=0;CCCC\x1b\\'
        for byte in command:
            boundary.feed(bytes([byte]))
        boundary.feed(image(18) + image(99, quiet=2))
        self.assertEqual([image_id for _, image_id in boundary.graphics_replies], [17, 18])
        self.assertFalse(boundary.graphics_more)
        self.assertTrue(boundary.reply(ack(17)))
        self.assertFalse(boundary.reply(ack(17)))
        self.assertEqual([image_id for _, image_id in boundary.graphics_replies], [18])

    def display_without_font(self):
        display = KittyDisplay.__new__(KittyDisplay)
        display.boundary = Boundary()
        display.visible = True
        display.visible_passive = False
        display.cursor = (6, 2)
        display.last = None
        display.image_id = 999
        return display

    def test_duplicate_id_rejection_is_atomic_for_ownership_and_visible_overlay(self):
        display = self.display_without_font()
        display.boundary.feed(image(17))
        boundary = display.boundary
        pending = list(boundary.graphics_replies)
        with self.assertRaises(ValueError):
            display.output(b'prefix' + image(18) + image(17))
        self.assertIs(display.boundary, boundary)
        self.assertEqual(boundary.graphics_replies, pending)
        self.assertEqual(boundary.graphics_serial, 1)
        self.assertTrue(display.visible)
        self.assertTrue(boundary.safe)
        self.assertEqual(display.clear(), b'\x1b_Ga=d,d=I,i=999,q=2\x1b\\')

    def test_ownership_limit_rejection_does_not_mutate_prior_pending_list(self):
        display = self.display_without_font()
        for image_id in range(1, 65):
            display.boundary.feed(image(image_id))
        boundary = display.boundary
        before = list(boundary.graphics_replies)
        with self.assertRaises(ValueError):
            display.output(image(65))
        self.assertIs(display.boundary, boundary)
        self.assertEqual(boundary.graphics_replies, before)
        self.assertEqual(boundary.graphics_serial, 64)
        self.assertTrue(display.visible)


if __name__ == '__main__':
    unittest.main()
