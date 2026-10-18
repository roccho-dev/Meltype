# Direct Input独立完成 — tty-split-20261013-v1 / B

## 目的

bcon IME OFF → Meltype → 固定した子PTYで、日英入力・候補表示・選択・取消・確定・復旧を完成させる。
cdp-tty/Chromeの完成を待たず、実Shell・Editorで独立に実証する。Xという中間製品は追加しない。
Fcitx5・IBus・SKK・別の言語判定器を新しい入力経路の必須依存にしない。

## 再利用元と実装

既存 [PR #2](https://github.com/roccho-dev/Meltype/pull/2) の
`d673872560f7e6b3b20fed1452f6949c55c722f1` を移行元として固定する。
本branchはmainから派生し、旧PR #2の全体mergeを前提にしない。
既存Core/NativeAOT/TTY/Kitty候補/Proposal portと回帰試験を再利用した。
B commit `8c9f009b6e0f9c421f957b4e8bd9ab68bf3ff3dd` で製品コードを切り出し、
Linux run `38350288872` でChrome/cdp-ttyなしの実bcon＋Bash/nano試験が成功した。
後続headは自身の同版checkが必要であり、この成功を流用して完成扱いしない。

| 移行元 | 分割後の責務 |
|---|---|
| `linux/tty/meltype_tty.py`, `kitty_display.py` | Bが入力・表示・寿命・観測可能な入力保護を所有。別の画面モデル・候補計算は増やさない |
| `src/Meltype.Core/Composition/MeltypeSession.cs`, `src/Meltype.Mac.Native/ProposalExports.cs` | 既存Coreと最小の公開接点を維持 |
| TTY回帰・実Native・実CLI試験 | Bの同版必須checkへ継承 |
| `linux/tty/tests/bcon.py` / `bcon.nix` | BはShell/Editor単独。Browser/DOM結合の旧原本はPR #2固定commitに保持し、Cへ引継ぐ |
| `.github/workflows/linux.yml`, `linux/build.sh` | Bの同版配布・実bcon単独試験。Cの結合checkとの対応を維持 |

## Bの受入と能力の区別

- 実NativeAOT/Mozc、実bcon画面、仮想物理キーと実物理キーの証拠を区別する。
- Shell/Editorで英数・日英混在・候補選択/取消・確定・キー/貼付け・resize・終了/復旧を検証する。
- Proposalの元ID・型・世代・取消・一度だけ選択を保持し、操作候補を端末文字として実行しない。
- 確定前は既存Meltypeの編集、確定後は子Editorの実削除・native Undo/Redo・複数行を検証する。汎用PTYの未観測なdeleteBeforeを推測のキーで実装しない。
- canonical no-echo入力は変換・候補取得を通さない。raw no-echoは既定で素通し、`--raw-text`は既知の本文アプリへの明示許可とする。rawパスワード欄やアプリ内caret変更まで安全と主張しない。
- 実Shared Provider/source bindingとローカルfixtureを分離し、必要な実接続の証拠を残す。
- PR #3のB1〜B7を弱めない。独立review・必要な物理端末・実Provider・C側の移設受領を試験成功で代替しない。

## Cへの受渡しと旧PR

Browserへの日本語・型付き候補のDOM読戻し、同一Chrome Target、Viewer再接続、画面競合はCが所有する。
CはBのコードを複製せず、候補版commit・artifact hash・契約を消費する。
旧原本はPR #2の `d673872560f7e6b3b20fed1452f6949c55c722f1` にある
`linux/tty/tests/bcon.py`, `bcon.nix`, `.github/workflows/linux.yml` と
Linux run `38337495446` の失敗・画像証拠。Bの単独試験でこれらを消さない。
移設先の実試験/CI入口、元条件との対応、旧失敗の維持を確認するまで引継ぎ完了とはしない。
BのmergeとCの全体合格を同一視しない。旧PR #2のclose/mergeは行わない。
新しい担当・独立R・権限は創作しない。恒久OS設定・実ユーザーBrowser/Profile・認証・旧editsは変更しない。
