# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 roccho-dev

# 実際の配布 ZIP を使い、外に置かれた通常版の更新状態を portable が使わないことを確かめる。
# キーボードフックは掛けない。通常版のインストール・起動登録・IME 登録もしない。
param([Parameter(Mandatory)][string]$Archive, [Parameter(Mandatory)][string]$Version)
$ErrorActionPreference = 'Stop'
$archivePath = (Resolve-Path -LiteralPath $Archive).Path
$checker = Join-Path $PSScriptRoot 'check_windows_portable.py'
& python $checker $archivePath --version $Version
if ($LASTEXITCODE -ne 0) { throw 'ポータブル版の ZIP 契約に一致しません。' }

$probeRoot = Join-Path ([IO.Path]::GetTempPath()) ('meltype-portable-' + [guid]::NewGuid().ToString('N'))
$binaryRoot = Join-Path $probeRoot 'binary'
$stateRoot = Join-Path $probeRoot 'state'
$stagedRoot = Join-Path $probeRoot 'staged'
$priorData = $env:MELTYPE_DATA_DIR
$priorGui = $env:DOTNET_DISABLE_GUI_ERRORS
function Snapshot([string]$Directory) {
    return @(Get-ChildItem -LiteralPath $Directory -Recurse -File | Sort-Object FullName | ForEach-Object {
        $_.FullName.Substring($Directory.Length) + ':' + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    }) -join "`n"
}
try {
    New-Item -ItemType Directory -Path $binaryRoot, $stateRoot, $stagedRoot -Force | Out-Null
    Expand-Archive -LiteralPath $archivePath -DestinationPath $binaryRoot
    $updateRoot = Join-Path $stateRoot 'update'
    New-Item -ItemType Directory -Path $updateRoot | Out-Null
    # 有効な通常版の更新状態も、portable からは読まない・削らない・実行しない。
    Set-Content -LiteralPath (Join-Path $stagedRoot 'install.ps1') -Value "throw 'portable must never execute this staged installer'" -Encoding UTF8
    [IO.File]::WriteAllText((Join-Path $updateRoot 'ready.txt'), "9999.0.0`t$stagedRoot`troccho-dev/Meltype")
    [IO.File]::WriteAllText((Join-Path $updateRoot 'applying.txt'), '9999.0.0')
    $beforeBinary = Snapshot $binaryRoot
    $beforeState = Snapshot $stateRoot
    $env:MELTYPE_DATA_DIR = $stateRoot
    $env:DOTNET_DISABLE_GUI_ERRORS = '1'
    $exe = Join-Path $binaryRoot 'Meltype.exe'
    $actualVersion = ((Get-Item -LiteralPath $exe).VersionInfo.ProductVersion -split '\+')[0]
    if ($actualVersion -cne $Version) { throw "実行ファイルの版が違います: $actualVersion" }
    $report = Join-Path $probeRoot 'selftest.txt'
    $process = Start-Process -FilePath $exe -ArgumentList '--selftest', "`"$report`"" -PassThru
    if (-not $process.WaitForExit(120000)) { $process.Kill(); throw 'portable 自己診断が終わりません。' }
    if ($process.ExitCode -ne 0) { throw "portable 自己診断に失敗しました: $($process.ExitCode)" }
    $result = Get-Content -LiteralPath $report -Raw -Encoding UTF8
    if ($result -notmatch 'OK   ポータブル版の更新経路:' -or $result -match '(?m)^FAIL ') { throw $result }
    if ((Snapshot $binaryRoot) -cne $beforeBinary) { throw 'portable が配布 tree を書き換えました。' }
    if ((Snapshot $stateRoot) -cne $beforeState) { throw 'portable が通常版の更新状態を書き換えました。' }
    if (Test-Path -LiteralPath (Join-Path $binaryRoot 'data')) { throw '保存先 override より配布 tree を使いました。' }
    Write-Output 'PASS: portable の実行版・保存先 override・通常版更新からの分離'
} finally {
    $env:MELTYPE_DATA_DIR = $priorData
    $env:DOTNET_DISABLE_GUI_ERRORS = $priorGui
    Remove-Item -LiteralPath $probeRoot -Recurse -Force -ErrorAction SilentlyContinue
}
