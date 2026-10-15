// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 Yukishiro

namespace Meltype.Tests;

/// <summary>自動更新は、この fork のリリースだけを使う (更新元の定数・ready.txt の取得元)。</summary>
internal static class UpdaterTests
{
    [Test]
    public static void UpdateRepository_IsTheRepositoryThatBuiltIt()
    {
        // CI (GitHub Actions) では、ビルドしているリポジトリ (GITHUB_REPOSITORY) を更新元にしていることを確かめる
        var repository = Environment.GetEnvironmentVariable("GITHUB_REPOSITORY");
        if (string.IsNullOrEmpty(repository)) return;
        Assert.Equal(repository, AppInfo.UpdateRepository, "更新元はビルドしたリポジトリ");
    }

    [Test]
    public static void ReadyFile_IsUsedOnlyWhenItCameFromTheUpdateRepository()
    {
        const string repository = "roccho-dev/Meltype";
        Assert.True(Updater.ParseReady("1.2.1\tC:\\update\\1.2.1", repository) is null, "取得元の無い前の形式 (2 列) は使わない");
        Assert.True(Updater.ParseReady("1.2.1\tC:\\update\\1.2.1\tyksr-melt/Meltype", repository) is null, "ほかのリポジトリのものは使わない");
        Assert.True(Updater.ParseReady("1.2.1.2\tC:\\update\\1.2.1.2\troccho-dev/meltype", repository) is null, "取得元が完全に同じでなければ使わない");
        Assert.True(Updater.ParseReady("1.2.1.2\tC:\\update\\1.2.1.2\troccho-dev/Meltype\textra", repository) is null, "列が多いものは使わない");
        Assert.True(Updater.ParseReady("", repository) is null, "空のものは使わない");
        var ready = Updater.ParseReady("1.2.1.2\tC:\\update\\1.2.1.2\troccho-dev/Meltype\r\n", repository);
        Assert.Equal("1.2.1.2", ready?.Version, "同じ更新元のものは使う (版)");
        Assert.Equal(@"C:\update\1.2.1.2", ready?.Folder, "同じ更新元のものは使う (展開した場所)");
    }
}
