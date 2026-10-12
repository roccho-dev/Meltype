# Direct TTY: transport proof (Draft)

Goal: run the **existing** Meltype NativeAOT session inside one child PTY without
Fcitx5, IBus, SKK, or a new language detector. This is a bounded **experimental
transport**, not a production IME and not a replacement for the existing IBus path.

```mermaid
flowchart TB
  subgraph HOST["NixOS / bcon | IME OFF"]
    B["bcon | raw key bytes"]
    subgraph P["Meltype fork | one child PTY"]
      K["InputBridge | parse and validate"]
      M["Existing NativeAOT | language and conversion"]
      T["Fixed child PTY | CLI / editor"]
      K <--> M
      K -->|"validated commit or original input"| T
    end
    B --> K
  end
```

## Current proof

- Builds on the already merged Meltype PR #1 **via main**, without changing
  the shared C# Core or the Windows/IBus entry points.
- Uses the existing meltype_handle_key / meltype_commit NativeAOT C ABI.
- Does not initialize NativeAOT until **after** the child fork; the runtime is
  never inherited by the child. Each invocation owns precisely one child PTY.
- Stops before output for invalid results, deleteBefore edits, unconfirmed
  control characters, or incomplete input sequences. Bracketed paste is bounded
  and only emitted after the entire closing marker has been seen.
- No background daemon, separate ranker, model, database, SKK engine or Fcitx5.
- Tests include real POSIX PTY isolation and a C-ABI test shim. These tests do
  **not** establish real Meltype conversion quality or NixOS/bcon E2E.

## Trial entry (disposable host only)

Run the existing Python 3 script with --native pointing to the actual
libMeltypeNative.so, --mozc to the same build's meltype_mozc_helper,
and -- followed by the child command. Both must come from the same pinned
Linux build, and stdin/stdout must be a physical TTY.

It is deliberately not installed by linux/build.sh and not a NixOS service.

## NOT DONE

- **No preedit or candidate overlay.** Meltype's view is kept away from the
  child's stdin, but is not yet displayed. This is not a usable replacement IME.
- Meltype may request deleteBefore after sending earlier ASCII. The proof
  rejects that request rather than synthesizing unverified shell backspaces.
  A safe deferred-input or proven target-edit contract is required.
- No typed Proposal port on Linux NativeAOT; no old-ID/ABA proof.
- Extended Kitty keys, wide Unicode input events, shell mode boundaries,
  output overlays, native Undo and cdp-tty are not proven.
- No exact native build + actual bcon TTY + restart/readback combination yet.

Design: [ADRS #618](https://github.com/roccho-dev/adrs/issues/618).
Browser ownership: [ADRS #615](https://github.com/roccho-dev/adrs/issues/615).
This draft does not authorize deployment, merge, or retiring edits.
