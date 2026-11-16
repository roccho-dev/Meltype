# Linux 版

利用者向けの説明 (インストール・使い方・困ったとき) は [docs/LINUX.md](../docs/LINUX.md) にあります。

| 部分 | 中身 |
| --- | --- |
| IBus のエンジン (`ibus-engine-meltype`, Python) | IBus からキーを受け取り、判定の本体に渡して、変換中の文字・候補・確定を入力欄に反映する |
| fcitx5 のアドオン (`fcitx5/`, C++) | fcitx5 からキーを受け取り、判定の本体に渡す |
| Direct TTY (`tty/`, Python) | 固定した子 PTY のキーと候補表示を受け持つ fork の拡張。bcon・Shell・Editor の確認は [tty/README.md](tty/README.md) |
| 設定画面 (`meltype-settings`, Python・GTK 4) | `config.schema.json` から画面を組み立てて、`config.json` を書く |
| 判定の本体 (`libMeltypeNative.so`) | Windows 版と共通の C# の部分 (`src/Meltype.Core`) を NativeAOT で Linux 用のライブラリにしたもの |
| 漢字変換 | Mozc の変換ヘルパー (`native/mozc`) |

全体の仕組み・テストは [docs/dev/DEVELOPMENT.md](../docs/dev/DEVELOPMENT.md) にあります。

## ビルドする

Ubuntu・Debian (WSL を含む) と Fedora でビルドできます。`--setup` は、apt か dnf でビルドに要るものを入れます。

```bash
bash linux/build-cli.sh --setup --all
```

`--all` は、本体のテスト → Mozc のヘルパーのビルド → パッケージの作成までをまとめて行います。できたものは `linux/build/Meltype-linux/` に入ります。

この fork の版は `src/Meltype.Core/Meltype.Core.csproj` の `<Version>` から読みます。
上流の数字 3 つに加え、fork の数字 4 つ (`1.6.0.1` など) も使えます。
タグから作るときは、タグの `v` の後ろとこの値が一致しなければ止まります。
Windows の版も同じ値にそろえ、成果物の名前だけで同じソースと判断しないでください。

Mozc のヘルパーだけを既存の配布物から使う場合は、取得元のタグ・ZIP の SHA-256 と、
ヘルパーの SHA-256 を記録します。`native/mozc/` のソースと固定した Mozc の commit が
取得元と同じかを確認し、変わっていればヘルパーも作り直します。
NativeAOT の本体と `tty/` は、使う fork の同じ commit から作ります。

一部だけ行うときは、次のように分けて実行できます。

```bash
linux/build-cli.sh --test            # テストだけ
bash linux/build-cli.sh --build      # ビルドだけ
linux/build-cli.sh --package         # パッケージを作るだけ
linux/build-cli.sh --mozc --package  # Mozc のヘルパーも作り直してパッケージにする
```

## Direct TTY の同版確認

上流 v1.6.0 への追従後は、Direct TTY も IBus・fcitx5 と同じ `ImeHost` の
`meltype_ime_create`・`meltype_ime_handle`・`meltype_ime_destroy` を使います。
出来事の版は 1、インスタンスは `tty` です。前の C API のライブラリには差し替えません。
候補は既存 Core が決め、TTY は前の文字を削除できるとは申告しません。

ビルドした成果物を使って、次を確認します。

```bash
dotnet run --project src/Meltype.Core.Tests -c Release -- DirectTtySessionTests
python3 -m unittest discover -s linux/build/Meltype-linux/tty/tests -p 'test_*.py'
python3 linux/build/Meltype-linux/tty/tests/native_tty.py "$PWD/linux/build/Meltype-linux"
python3 linux/build/Meltype-linux/tty/tests/native_proposals.py "$PWD/linux/build/Meltype-linux"
python3 linux/build/Meltype-linux/tty/tests/native_display.py "$PWD/linux/build/Meltype-linux"
```

`linux/tty/package.nix` は、この展開済み成果物から Direct TTY に必要な
NativeAOT・Mozc・TTY・ライセンスだけを選びます。fcitx5 の開発用パッケージがなくても
Direct TTY の成果物は作れます。VM を動かせる Nix 環境では、さらに次を実行します。

```bash
nix-build linux/tty/tests/bcon.nix --argstr packagePath "$PWD/linux/build/Meltype-linux"
```

PTY と制御した端末応答の試験だけでは、bcon の画面や実機での操作まで確認したことにはしません。
変換エンジンの終了はプロセスが受け持ちます。試験用データを一時ディレクトリに置く場合は、
`meltype_shutdown` を呼んでからディレクトリを削除し、学習保存や背景処理との競合を避けます。

## インストールする

```bash
linux/build-cli.sh --install
```

`linux/build/Meltype-linux/` から `.deb` (apt のディストリビューション) か `.rpm` (Fedora) を作って入れます。版には `+dev.<日時>` が付くので、同じ版で何度入れ直しても、同じ版のリリース版を入れてあっても、更新として入ります。

`+dev` の版は、同じ版のリリース版より新しい扱いになります。リリース版に戻すときは、ダウングレードを明示します。

```bash
sudo apt install --allow-downgrades meltype=<版>   # apt のリポジトリから。ファイルなら ./meltype_<版>_amd64.deb
sudo dnf downgrade meltype                          # dnf のリポジトリから。ファイルなら ./meltype-<版>-1.x86_64.rpm と ./meltype-fcitx5-<版>-1.x86_64.rpm
```

入力ソースの一覧に Meltype が出てこないときは、Linux のセッションを入り直してください。
エンジンの動きを調べるときは、次のように手で起動するとログが見られます。

```bash
ibus restart
sleep 2
pkill -f ibus-engine-meltype || true
/opt/meltype/ibus-engine-meltype --version   # 版だけ確認するとき
/opt/meltype/ibus-engine-meltype
```
