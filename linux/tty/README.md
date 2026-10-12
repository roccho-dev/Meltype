# Direct TTY — existing Meltype Core, per-PTY input and Kitty candidate images

Goal: bcon IME OFF → existing Meltype NativeAOT/Mozc → one bound child PTY,
without Fcitx5, IBus, SKK, a second language detector or a resources change.
**Draft / PRODUCT_NOT_READY.** The implemented candidate-image path must pass
actual bcon scanout acceptance; protocol/PNG tests alone are not that evidence.

```mermaid
flowchart TB
  subgraph HOST["Test host | no permanent configuration change"]
    B["bcon | IME OFF"]
    subgraph PRODUCT["Meltype | one bound child PTY"]
      K["InputBridge | capture, retain and route"]
      M["Existing NativeAOT and Mozc"]
      U["KittyDisplay | transient owned candidate image"]
      T["Bound child PTY | CLI / editor / cdp-tty"]
      K <--> M
      M --> U
      K --> T
      U -->|"image plane, no shadow text grid"| B
    end
    B --> K
  end
```

## Implementation

The Core/Windows/IBus sources are unchanged. NativeAOT initializes after fork;
a startup pipe holds child exec until native, font and graphics-capability checks
succeed. An unsupported terminal does not silently run invisible composition.
Paste cannot overtake pending composition. Cursor/device/graphics replies are
opaque, not a request to commit. Native candidate arrows and F6–F10 work during
composition. Kitty CSI-u/modifyOtherKeys use the same Core; consumed key releases
are suppressed, and unconsumed protocol bytes survive unchanged. Escape timing is
independent of child output. Cleanup restores the terminal and reaps its own child.

The Kitty display uses an existing CJK font with Pillow. It requests the actual
cursor in serial order, pauses user input until that report is received, and does
not forward later child output while its own query is pending. A finite boundary
parser prevents insertion inside UTF-8/control/image sequences. Only one owned
image is deleted; child cells, saved cursor slots and a full shadow grid are not
owned or copied. Origin-relative cursor mode currently fails explicitly.

This is the bcon-specific alternative to copying ttyskk's SKK-coupled frontend:
reuse bcon's graphics plane rather than maintain another terminal screen model.
It is not a claim of universal VT/terminal compatibility or independently accepted
optimality. A concrete failed case can require revisiting the choice.

## Same-build checks and delivery

linux/build.sh includes tty source/tests with the same NativeAOT library and Mozc
helper. The existing workflow requires 51 controlled regression tests, nine real
Core/Mozc/bound-PTY cases, and a real CLI/native/PTY/PNG transport test before ZIP
upload. Real-native cases use fresh processes and temporary HOME/XDG paths so
learning/path caches cannot contaminate another case. Missing real artifacts fail.
The CLI test records hashes and checks candidate images, selected child text, exit
status and restored termios. Its terminal replies are controlled, not actual bcon.

The additional standard NixOS VM test reuses the existing pinned resources bcon
package read-only. It runs unchanged bcon on a real VM VT, sends virtual physical
keys, takes QEMU scanout PNGs, reads actual child bytes and checks terminal recovery.
Nix packaging adjusts ELF paths using standard autoPatchelfHook; this runtime
adaptation is not called byte-identical to the Ubuntu artifact. No production
configuration, credentials, browser profile, source or resources PR is changed.

For disposable trials, the script takes --native, --mozc, --font and -- followed by
the child command. Python 3, Pillow and an existing CJK font are runtime dependencies.
No fonts are copied into the product. --transport-proof explicitly allows nonvisual
transport tests only. Do not use the experimental entry around valuable work.

## Remaining acceptance

- Actual bcon pipeline result and visual review; a configured job is not a PASS.
- Safe native deleteBefore/reconversion and supported multiline editing: still
  rejected, not emulated with unverified shell backspaces. Native Undo unproven.
- Linux typed Proposal port: original ID/type, generation/cancel and one-shot choice.
- App mode/focus/VT changes and extended-key edge cases beyond the fixed tests.
- Existing cdp-tty → same existing Headless Chrome target: Japanese DOM readback.
- Independent same-version review; self-checks are not an independent R.

Initial implementation and bcon evidence stay in this Meltype PR. resources owns
later permanent NixOS installation/distribution/recovery changes only if needed.
Design: https://github.com/roccho-dev/adrs/issues/618
Browser ownership: https://github.com/roccho-dev/adrs/issues/615
No merge, deployment, secret/profile change or old-edits retirement is claimed.
