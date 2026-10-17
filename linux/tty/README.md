# Direct TTY input — B / Meltype PR #3

B is `bcon (IME OFF) ↔ Meltype ↔ a fixed child PTY running a Shell or Editor`.
It has no Chrome/cdp-tty test dependency. Conversion remains the existing
Meltype Core/NativeAOT/Mozc implementation. There is no SKK, Fcitx5, second
ranker, daemon, or shadow terminal grid. Kitty images display candidates.

## Adopted main compatibility

The shared SessionResult encoder is the main encoder: one `notes` array,
positioned by candidate count, and the existing optional `expect` on text edits.
The TTY does not reinterpret `expect` as permission to erase child-owned text.
It uses main's `meltype_set_can_delete(..., 0)` before input, retaining atomic
rejection of any unsupported deletion that nevertheless reaches the boundary.
A matching NativeAOT artifact is required; an older library without that API
is not substituted. The original Proposal port and lifetime/cancel rules remain.

The ordinary Linux artifact keeps main's IBus/fcitx5 products and all their
existing tests. B's Nix output selects only NativeAOT, Mozc, TTY and notices
from that artifact, so the Direct Input runtime does not acquire addon, IBus,
Chrome or cdp-tty dependencies. The Shell/Editor VM still checks that absence.

## Reusable B package export

`linux/tty/package.nix` is the sole B-owned Nix wrapper of an immutable,
same-build `Meltype-linux` artifact. It accepts `{ pkgs, packagePath }` and
returns a derivation providing `bin/meltype-tty`, existing NativeAOT/Mozc
and font references. It reuses the exact recipe previously embedded in B's
VM test. It does **not** build an independent conversion engine or own bcon.

Raw no-echo interception is not enabled by the package. The isolated B
Shell/nano test now requests `--raw-text` explicitly per known text app.
An unknown or password input still defaults to bypassing capture.

C/resources #14 can consume this owner-authored package expression from a
pinned B source revision together with a separately verified immutable
artifact/source hash. This export is not a substitute for its source/version
admission and does not imply that C's integration tests have passed.

## Source and ownership

Reuse: old PR #2 at `d673872560f7e6b3b20fed1452f6949c55c722f1`.
The old PR and its Browser/DOM tests remain unchanged. Their receiver is
resources #14 (C); B's input-only suite is not the combined product's proof.
The split contract and original merge conditions are in `docs/split-direct-tty.md`
and PR #3. Physical-host, independent-review and provider evidence remain distinct.

| Capability | Owner |
| --- | --- |
| Japanese/English recognition, preedit, selection, cancel | Existing Meltype Core |
| Fixed child input route, keys, opaque paste, resize, exit | TTY adapter |
| Edits before commit | Existing composition controls; child text is untouched |
| Deletion, native Undo/Redo and newlines after commit | The child Editor; nano's actual file content is tested |
| Unknown `deleteBefore`, universal reconversion of committed text | Not supported without an observed edit capability; no guessed backspaces |

The first Enter consumes pending conversion; a later Enter can submit a shell
line or insert an Editor newline. The tests check that these are not confused.

## Direct Input (default)

By default (fork #16, upstream #816) keys reach the child one at a time, so its
Tab completion, history search and suggestions keep working. Ctrl+Space converts
the ASCII word just typed (letters after the last space or symbol, upstream #679):
meltype-tty sends that many BackSpaces (0x7f, what bcon's BackSpace key sends)
and gives the same letters to the Core's composition; selection and commit are
unchanged. Esc cancels the conversion and puts the word back.

The word is remembered only while the child's echo shows exactly those keys.
Tab, arrows, Enter, Ctrl keys, Esc, paste, a resize, focus or capture changes,
or any other child output forget it; BackSpace takes back one letter once its
echo is seen. When nothing is remembered, Ctrl+Space reaches the child (NUL).
So a conversion never erases more than the keys it saw echoed. `--compose`
restores the previous mode in which every key goes through a composition.

`--preview` (off by default, #19) also shows suggestions for that remembered
word while typing continues: it takes no key and writes nothing to the child.
They come from one replaceable source in semcmp's shape (roccho-org/ops#479:
`propose({input, state, focus})` returning `{id, representation, meaning,
evidence}`), run on its own thread so that no key waits for it: one query at a
time, once per word of at most 32 letters that has stayed the same for 150 ms.
An answer later than 0.5 s, or for an older word, is dropped. The first source
is the Core's own conversion in a second native session, always cancelled, so
the typing session is untouched and nothing is committed or learned; Core calls
are serialized, so only Ctrl+Space (which calls the Core) can wait for a query
in flight. The image is drawn only above the cursor line, never on it or below
where the shell edits and lists completions. While the child only echoes the
typed keys it stays, and new suggestions replace it in place under the same
image id; until the new word's answer arrives the previous suggestions stay
dimmed and labelled with their own word and "…", so they never pass for the
current word's. Any other child output (a completion list, a new line, a
redraw), an echo that would wrap, the word ending, or Ctrl+Space removes it
first. A late or out-of-range cursor reply, or an unusual terminal
mode, turns previews off (the reason is kept) rather than stopping input.
Without `--preview` nothing differs from 1.2.1.5: in particular the candidate
image never draws the Core's own key hint. `--preview-fd` explicitly records,
in an append-only file, what was shown, where, and when it was hidden; it holds
the previewed word and is off by default, like `--selection-fd`.

While a composition is shown, Ctrl+U/P/O go to the Core's existing switch to
hiragana / full-width / half-width (as Windows Meltype's F6/F9/F10), without
committing. Ctrl+I (katakana) is included only when Kitty keys tell it apart;
the legacy Ctrl+I byte is Tab. With no composition, every Ctrl key reaches the
child unchanged, so Readline, vim and nano keep their Ctrl commands.

## Capture and privacy

Canonical input with ECHO disabled bypasses conversion and Proposal lookup.
When capture becomes unavailable, pending composition and unsent/received
Proposal work are cancelled. A request already sent cannot be recalled, but its
response is invalidated. Privacy tests use fabricated strings, not credentials.

Raw no-echo input cannot be identified as an Editor versus a password field from
the PTY alone. It therefore bypasses capture by default. `--raw-text` explicitly
authorizes a known text app, as in the isolated Readline/nano trials; it is not a
claim about every later field in an arbitrary app. Canonical no-echo protection
still applies. Unobserved application-caret changes remain outside that guarantee.

Entry flags: `--native`, `--mozc`, an existing CJK `--font`, and optionally
`--raw-text` for a known text app, `--compose` for the composition mode and
`--preview` (with `--preview-fd`) for #19's suggestions. `--transport-proof` is explicitly nonvisual and
for testing, not normal use. Font files are not copied into this repository.

## Optional shared Proposal port

`MELTYPE_PROPOSAL_URL` explicitly enables local HTTP. With it unset, no provider
request is sent. The wire is request `{generation, raw}` and response
`{generation, raw, proposals}` preserving each original `id`, `representation`,
`meaning`, and `evidence`. Only text meaning is inserted; action candidates are
not terminal commands. The native port retains selection identity, not an effect
or adoption receipt. Original ordering, generation/cancel/one-shot checks belong
to the existing Core, not a second ranker here.

There is one active request, one replaceable pending request and one response.
Callbacks enter native code on the input thread. No credentials, redirects or
proxies forward captured text. Changed/unknown target epochs reject selection;
a fixed PTY identity is not evidence of an invisible GUI caret.

`ops/packages/semcmp` at `29be7a2d33c85f99ac693dd51d2ecd6ebbbaf8fe` is the shared
calculation source. Its README distinguishes controlled composition from real
provider quality and public consumer wiring. The local HTTP fixtures here do
not prove that external integration. Keep its required real binding as an explicit
acceptance item rather than duplicating candidate production in Meltype.

## Checks and evidence

The Linux workflow runs exact-source regressions and same-build package checks:
`test_*.py`, real Core/Mozc/PTY (`native_tty.py`), real CLI/native/PTY/PNG with
controlled terminal replies (`native_display.py`), and real NativeAOT typed
Proposal checks (`native_proposals.py`). Controlled replies are not bcon evidence.

`bcon.nix`/`bcon.py` add a real unchanged bcon in a NixOS test VM with virtual
physical keys and actual screenshots. The guest has Bash Readline and nano but
no Chromium or cdp-tty. Tests check mixed input, candidate selection, native
Editor deletion/Undo/Redo, multiple lines, local typed candidates, hidden input,
and same-phase termios restoration. Learning/config paths are isolated per trial.

At B commit `8c9f009b6e0f9c421f957b4e8bd9ab68bf3ff3dd`, Linux run `38350288872`
passed the input-only suite including Shell input and nano editing. Later source
changes require their own same-head result; historical passes are not transferred.

B1–B7 in PR #3 remain the merge conditions. Independent review, needed physical
host evidence, actual shared-provider binding, and C's verified migration are not
implied by VM/fixture success. No merge, deployment or old-edits retirement occurs.
