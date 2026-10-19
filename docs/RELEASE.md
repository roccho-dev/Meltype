<a name="リリースの手順"></a>
<img src="images/headings/release/title.svg" alt="リリースの手順" height="80">


<br>

<a name="版を出す"></a>
<img src="images/headings/release/01.svg" alt="版を出す" height="53"><br>


この fork (roccho-dev/Meltype) は、GitHub のリリースの zip だけで配布します。インストーラー (setup.exe)・winget・Scoop・Homebrew には出しません。

1. 版を上げる
   - `src/Meltype/Meltype.csproj`・`src/Meltype.Core/Meltype.Core.csproj` の `<Version>`。版は数字 4 つ (`1.2.1.2` など。`System.Version` で比べられる形) にする
   - `mac/Resources/Info.plist` の `CFBundleShortVersionString` と `CFBundleVersion` (Mac 版の表示の版は Info.plist から読みます)
2. コミットして main に入れ、同じ版のタグを付けて push する: `git tag v1.2.1.2 && git push origin v1.2.1.2`
3. GitHub Actions が Windows (fork-build.yml)・Mac (mac.yml)・Linux (fork-linux.yml) の配布物を作り、リリースに付ける
   - Windows: `Meltype-<版>-windows.zip`。fork-build.yml は、タグの版とビルドした `Meltype.exe` の版 (ProductVersion) が違えば止まり、zip にビルドの出自の証明 (artifact attestation) を付けます
   - Mac: `Meltype-<版>-mac.zip`、Linux: `Meltype-<版>-linux.zip` (どちらもプレビュー版)
4. リリースのページにリリースノートを書く (プレリリースにはしない。自動更新は最新のリリース (latest) だけを見ます)
5. 使っている人の Meltype (Windows) は、自動で新しい版に更新されます (この fork のリリースだけを見ます)

配布物の出自は `gh attestation verify <zip> --repo roccho-dev/Meltype` で確かめられます。

upstream の `build.yml`・`linux.yml` は upstream の版のまま置き、この fork では Actions の設定で止めています (`gh workflow disable build.yml`、`gh workflow disable linux.yml`)。

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
