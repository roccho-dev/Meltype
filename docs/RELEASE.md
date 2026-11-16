<a name="リリースの手順"></a>
# リリースの手順


<br>

<a name="版を出す"></a>
## 版を出す


この fork (roccho-dev/Meltype) は、GitHub のリリースの zip だけで配布します。インストーラー (setup.exe)・winget・Scoop・Homebrew には出しません。

1. `src/Meltype.Windows/Meltype.Windows.csproj`・`src/Meltype.Core/Meltype.Core.csproj` の版を数字 4 つ (`1.6.0.1` など) にそろえ、`docs/release-notes/<版>.md` を書く。
2. PR でテストと候補成果物を検証する。`fork-build.yml` は通常版・ポータブル版の Windows ZIP、`fork-linux.yml` は Direct TTY を含む Linux ZIPを作る。PR からは公開しない。
3. 公開する source commit を固定し、その commit で両方の workflow を手動実行する。成功した run と `meltype-windows-candidate` / `meltype-linux-candidate` の artifact ID、各 `*-candidate.json` の source・版・hashを確認する。Windows の候補では実 ZIP の portable 起動・保存先・残存する通常版更新からの分離も検査する。
4. 検証した source commit に同じ版のタグを付ける。タグの push だけでは公開されない。
5. `fork-publish.yml` の手動実行へ、タグ・source commit・両方の run ID / artifact ID を渡す。公開入口は同一sourceの成功した自repoの実行と、出自・版・hashを照合し、検証済みの同じ ZIP を再ビルドせずに使う。初期値は `prerelease=true`・`make_latest=false`。
6. 正式版の latest へ進めるときは、その作用と利用先への影響を確認したうえで `prerelease=false`・`make_latest=true` を明示する。通常版の自動更新はこの fork の latest を見る。ポータブル版の更新は配布側が担当する。

公開結果が不明なときは、そのタグの Release と asset の実 identity を先に読む。同じ操作の再実行や既存 asset の上書きで埋め合わせない。

この候補昇格入口の対象は Windows / Linux。Mac の source と既存の Release は保持し、未検証の Mac 成果物を同じ候補の成功として扱わない。

配布物の出自は `gh attestation verify <zip> --repo roccho-dev/Meltype` で確かめられます。

upstream の公開・管理 workflow にはこの fork で副作用を起こさない条件を置き、fork の候補検証・公開入口を使います。`.github/upstream-base.json` が選んだ上流 tag / commit を固定し、fork-surface の手動実行は最新 stable の観測を別に報告します。新しい版を観測しただけでは、選択済み source や成果物を置き換えません。

<br>

<a name="同梱の-net-の更新"></a>
## 同梱の .NET の更新


配布物の .NET の版は、どこにも固定していません。ビルドのたびに setup-dotnet (`10.0.x`) がその時の最新の SDK とランタイムを入れます。
Windows は `Build-Package.ps1` が、入っている最新の 10.0 のランタイムを `app\dotnet` に同梱し、Mac・Linux は NativeAOT で組み込みます。
なので、.NET に新しいパッチ (セキュリティの修正など) が出たら、コードを変えずに版を上げてリリースし直せば済みます。
.NET のメジャー版を上げる (net10.0 → net11.0) ときは、`*.csproj` の `TargetFramework` と、ワークフローの `dotnet-version` を手で変えます。

<br>

<a name="github-の設定"></a>
## GitHub の設定


- Settings → Security → **Private vulnerability reporting** を ON にする ([SECURITY.md](../SECURITY.md) の報告先)
- Settings → General → Features → **Issues** を ON にする (アプリの「不具合の報告・提案...」はこの fork の Issue を開きます)
- Settings → Branches → main のブランチ保護で、必須のチェックに **build**・**精度の比較**・**辞書の形式** を入れる

## 設定・学習データを保って版を戻す

実行ファイルの版戻しと、データを古い時点へ戻す操作は分ける。1.2.1.11 の設定は version 5、1.6.0 系は version 10 で、旧版の設定保存は新版で増えた項目を残さない。旧版と新版が同じデータフォルダーへ交互に書いても、設定が完全に往復するとは扱わない。

最低限の戻し方は、現在のデータを保持し、旧版を別のコピーで動かす形にする。

1. 現在の版・成果物 SHA-256・実際のデータ保存先を記録する。アプリと変換ヘルパーを止め、最後の書き込みを終わらせる。
2. **更新後の現在の**データフォルダー全体を、新しい保存先 `post-upgrade` へコピーする。更新前の checkpoint とは別に残し、このコピーをアプリの書き込み先にしない。
3. `post-upgrade` を、存在しない別の保存先 `rollback-working` へコピーする。検証済みの旧版は `MELTYPE_DATA_DIR` をこの保存先に指定して動かす。Windows ではその起動プロセスの環境変数、Linux ではそのコマンドの環境変数にする。更新を基盤が管理する端末では、旧版のアプリ内更新で別の版へ移らない既存の管理経路を使う。
4. 旧版で増えた辞書・学習は `rollback-working` に残る。新版へ戻すときは、これを新しい `resume-working` へコピーし、その `config.json` も別名で保存する。更新前の checkpoint をデータ全体に上書きしない。
5. 設定は、保管した `post-upgrade/config.json` と旧版で保存した設定を比較する。旧版で好みを変えていなければ、新版の設定を `resume-working/config.json` に使える。旧版で変えた好みがあれば、新版の設定画面で明示的に選び直す。未知の項目を自動でマージしない。辞書・学習は、旧版で増えた内容を持つ working copy を使う。
6. 新版を `resume-working` で開き、設定・登録語・学習と、その環境の実際の入力操作を確かめてから、採用するデータ保存先を決める。保管した二つの設定とデータは、この確認の前に消さない。

`MELTYPE_DATA_DIR` は 1.2.1.11 と 1.6.0 系で共通の保存先指定で、通常版とポータブル版の既定の場所より優先する。この手順だけで、端末の起動登録・IME 登録や Sandbox の表示 desktop が復旧したとは扱わない。それぞれの配信側で確認する。

### 隔離した旧版・新版の実データ検査

旧版と候補の checkout を指定し、実際の Core assembly で設定・ユーザー辞書・英日判定の学習を保存し直す。使うデータは検査専用の新しいフォルダーだけで、実ユーザーの保存先へは接続しない。

```sh
python3 tools/check-data-roundtrip.py \
  --baseline /absolute/path/to/Meltype-1.2.1.11 \
  --candidate /absolute/path/to/candidate \
  --dotnet /absolute/path/to/dotnet \
  --output /absolute/path/to/new-evidence-directory
```

出力の `report.json` は、source commit・変更差分と assembly の SHA-256、各段階のデータ SHA-256 を記録する。旧版による新版設定の項目消失を実際に再現し、別コピーでの版戻し後に、更新後の設定と版戻し中に増えた辞書・学習を保持できることを別に確かめる。これは旧版の設定編集を自動で引き継ぐ機構や、変換エンジン固有の全データ形式の互換保証ではない。

### source の再取得

採用した source ref は `git bundle create <新しい保存先.bundle> <採用ref>` で履歴ごと保管し、元 checkout に接続しない場所へその bundle から clone する。元と復元先の commit・tree を照合し、復元先から必要な build と入力検査を行う。source の復元と、実行時の成果物・設定・認証・端末状態の復旧はそれぞれ記録する。bundle を作っただけで旧 resources の削除準備が完了したとは扱わない。
