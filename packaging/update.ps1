# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Yukishiro

# Meltype の更新を確認して、新しい版があればダウンロードして展開しておく (Meltype が定期的に呼ぶ)。
#   update.ps1 -Repository roccho-dev/Meltype -CurrentVersion 1.2.1.1 -Directory %LOCALAPPDATA%\Meltype\update
# GitHub の最新のリリースに Windows 版の zip (Meltype-<版>-windows.zip、前の名前は Meltype-test-*.zip) があり、版 (タグ v1.2.1.2) が今の版より新しければ、
# <Directory>\<版> に展開し、<Directory>\ready.txt に「版 TAB 展開した場所 TAB 取得元のリポジトリ」を書く。インストールは Meltype が install.ps1 を呼んで行う。
# Meltype は、取得元が自分の更新元と同じ ready.txt だけを使う (取得元の無い前の形式・ほかのリポジトリのものは使わない)。
# GitHub が SHA-256 (digest) を公開していない zip は、確かめられないので使わない。
# 結果は標準出力に 1 行で返す: none (更新なし) / ready <版> / error <理由>

param(
    [Parameter(Mandatory)] [string]$Repository,
    [Parameter(Mandatory)] [string]$CurrentVersion,
    [Parameter(Mandatory)] [string]$Directory
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# 結果を UTF-8 で返す (Meltype が UTF-8 として読む)
[Console]::OutputEncoding = [Text.Encoding]::UTF8

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $headers = @{ 'User-Agent' = 'Meltype-Updater'; 'Accept' = 'application/vnd.github+json' }
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repository/releases/latest" -Headers $headers -TimeoutSec 30
    $latest = $release.tag_name -replace '^v', ''
    if ([version]$latest -le [version]$CurrentVersion) { Write-Output 'none'; exit 0 }

    $asset = @($release.assets | Where-Object { $_.name -like 'Meltype-*-windows.zip' }) + @($release.assets | Where-Object { $_.name -like 'Meltype-test-*.zip' }) | Select-Object -First 1
    if (-not $asset) { Write-Output "error リリース $latest に zip がありません"; exit 0 }
    if ($asset.digest -notlike 'sha256:*') { Write-Output "error リリース $latest の zip に SHA-256 がありません"; exit 0 }

    $target = Join-Path $Directory $latest
    $ready = Join-Path $Directory 'ready.txt'
    if ((Test-Path -LiteralPath $ready) -and ("$(Get-Content -LiteralPath $ready -Raw)".Trim() -ceq "$latest`t$target`t$Repository")) { Write-Output "ready $latest"; exit 0 }

    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
    $zip = Join-Path $Directory $asset.name
    Invoke-WebRequest -Uri $asset.browser_download_url -Headers @{ 'User-Agent' = 'Meltype-Updater' } -OutFile $zip -UseBasicParsing -TimeoutSec 600

    # GitHub が公開している SHA-256 と同じか (ダウンロードの途中で壊れていないか) を確かめる。
    $expected = $asset.digest.Substring(7).ToLowerInvariant()
    $actual = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { Remove-Item -LiteralPath $zip -Force; Write-Output 'error ダウンロードした zip のハッシュが違います'; exit 0 }

    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    Expand-Archive -LiteralPath $zip -DestinationPath $target -Force
    Remove-Item -LiteralPath $zip -Force
    if (-not (Test-Path -LiteralPath (Join-Path $target 'install.ps1'))) { Write-Output 'error zip に install.ps1 がありません'; exit 0 }

    # 古い版の展開先を片付ける
    Get-ChildItem -LiteralPath $Directory -Directory | Where-Object { $_.Name -ne $latest } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    [IO.File]::WriteAllText($ready, "$latest`t$target`t$Repository", (New-Object Text.UTF8Encoding $false))
    Write-Output "ready $latest"
}
catch {
    Write-Output "error $($_.Exception.Message)"
}
