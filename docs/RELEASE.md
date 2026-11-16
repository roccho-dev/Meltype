<a name="リリースの手順"></a>
<img src="images/headings/release/title.svg" alt="リリースの手順" height="80">


<br>

<a name="版を出す"></a>
<img src="images/headings/release/01.svg" alt="版を出す" height="53"><br>


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
<img src="images/headings/release/02.svg" alt="同梱の .NET の更新" height="53"><br>


配布物の .NET の版は、どこにも固定していません。ビルドのたびに setup-dotnet (`10.0.x`) がその時の最新の SDK とランタイムを入れます。
Windows は `Build-Package.ps1` が、入っている最新の 10.0 のランタイムを `app\dotnet` に同梱し、Mac・Linux は NativeAOT で組み込みます。
なので、.NET に新しいパッチ (セキュリティの修正など) が出たら、コードを変えずに版を上げてリリースし直せば済みます。
.NET のメジャー版を上げる (net10.0 → net11.0) ときは、`*.csproj` の `TargetFramework` と、ワークフローの `dotnet-version` を手で変えます。

<br>

<a name="github-の設定"></a>
<img src="images/headings/release/03.svg" alt="GitHub の設定" height="53"><br>


- Settings → Security → **Private vulnerability reporting** を ON にする ([SECURITY.md](../SECURITY.md) の報告先)
- Settings → General → Features → **Issues** を ON にする (アプリの「不具合の報告・提案...」はこの fork の Issue を開きます)
- Settings → Branches → main のブランチ保護で、必須のチェックに **build**・**精度の比較**・**辞書の形式** を入れる
