# Direct Input独立完成 — tty-split-20261013-v1 / B

## 目的

bcon IME OFF → Meltype → 固定した子PTYで、日英入力・候補表示・選択・取消・確定・復旧を完成させる。
cdp-tty/Chromeの完成を待たず、実Shell・Editorで独立に実証する。Xという中間製品は追加しない。
Fcitx5・IBus・SKK・別の言語判定器を新しい入力経路の必須依存にしない。

## 再利用元と分割手順

既存 [PR #2](https://github.com/roccho-dev/Meltype/pull/2) の
`d673872560f7e6b3b20fed1452f6949c55c722f1` を移行元として固定する。
本branchのbaseはmainであり、旧PR #2の全体mergeを先行条件にしない。
既存Core/NativeAOT/TTY/Kitty候補/Proposal portと回帰試験を再利用し、作り直さない。
現在の差分は分担契約のみ。製品コード・CIの切出しはまだ完了していない。

| 移行元 | 分割後の責務 |
|---|---|
| `linux/tty/meltype_tty.py`, `kitty_display.py`, `proposal_transport.py` | Bが入力・表示・寿命を所有。別の画面モデル・候補計算は増やさない |
| `src/Meltype.Core/Composition/MeltypeSession.cs`, `src/Meltype.Mac.Native/ProposalExports.cs` | 既存Coreと最小の公開接点を維持 |
| TTY回帰・実Native・実CLI試験 | Bの同版必須checkへ継承 |
| `linux/tty/tests/bcon.py` / `bcon.nix` | 入力単独部分はB、Browser/DOM結合部分はCへ引継ぐ。旧版を削って合格にしない |
| `.github/workflows/linux.yml`, `linux/build.sh` | Bの同版配布・実bcon単独試験。Cの結合checkとの対応を維持 |

## Bの受入

- 実NativeAOT/Mozc、実bcon画面、仮想物理キーと実物理キーの証拠を区別する。
- Shell/Editorで英数・日英混在・候補選択/取消・確定・キー/貼付け・resize・終了/復旧を検証する。
- Proposalの元ID・型・世代・取消・一度だけ選択を保持し、操作候補を端末文字として実行しない。
- 確定済み文字の削除・再変換・複数行/native Undoは実証済みの編集能力だけで行う。未達を単なる拒否で完成扱いしない。
- 不明な入力先、秘密入力、アプリ内caret変更を固定PTYだけで安全と推定しない。
- 実Shared Provider/source bindingとローカルfixtureを分離し、必要な実接続の証拠を残す。
- B自身の必須正常系・反例・同版CI/配布物・独立reviewが揃うまで未完成。

## Cへの受渡しと旧PR

Browserへの日本語・型付き候補のDOM読戻し、同一Chrome Target、Viewer再接続、画面競合はCが所有する。
CはBのコードを複製せず、候補版commit・artifact hash・契約を消費する。
移設先の実試験/CI接続、元条件との対応、旧失敗の維持を確認するまで旧PR #2の結合試験を削除しない。
BのmergeとCの全体合格を同一視しない。旧PR #2のclose/mergeは今回の起票では行わない。
新しい担当・独立R・権限は創作せず、切出し着手時に既存writer/head/進行中作用を確認する。
