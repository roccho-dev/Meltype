// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 Yukishiro

namespace Meltype.Tests;

/// <summary>
/// Windows の Proposal の口 (MELTYPE_PROPOSAL_URL)。打っている文字を送るので、未設定なら付けず、送り先は同じ PC (loopback) だけ。
/// </summary>
internal static class ProposalPortTests
{
    private const string Variable = "MELTYPE_PROPOSAL_URL";
    private static readonly Control Invoker = new();

    private static T WithEndpoint<T>(string? value, Func<T> run)
    {
        var before = Environment.GetEnvironmentVariable(Variable);
        Environment.SetEnvironmentVariable(Variable, value);
        try { return run(); }
        finally { Environment.SetEnvironmentVariable(Variable, before); }
    }

    private static Composition.ProposalHttpPort? From(string? value) =>
        WithEndpoint(value, () => Composition.ProposalHttpPort.FromEnvironment(Invoker, () => null));

    [Test]
    public static void FromEnvironment_Unset_IsNull()
    {
        Assert.True(From(null) is null, "未設定なら Proposal を付けない");
        Assert.True(From(" ") is null, "空白だけでも付けない");
    }

    [Test]
    public static void FromEnvironment_RejectsAnEndpointOffThisPc()
    {
        foreach (var url in new[] { "http://example.com/proposals", "https://192.168.1.2/proposals", "http://10.0.0.1:8080/", "ftp://127.0.0.1/" })
        {
            using var port = From(url);
            Assert.True(port is null, $"同じ PC の http(s) でない送り先は使わない: {url}");
        }
    }

    [Test]
    public static void FromEnvironment_AcceptsThisPc()
    {
        foreach (var url in new[] { "http://127.0.0.1:8765/proposals", "http://localhost:8765/proposals", "http://[::1]:8765/" })
        {
            using var port = From(url);
            Assert.True(port is not null, $"同じ PC の送り先は使う: {url}");
        }
    }

    [Test]
    public static void Tray_AttachesOnlyAUsablePort()
    {
        var attached = new List<Composition.IProposalPort?>();
        Composition.ProposalHttpPort? Attach(string? value) =>
            WithEndpoint(value, () => Composition.ProposalHttpPort.AttachFromEnvironment(attached.Add, Invoker, () => null));
        Assert.True(Attach(null) is null && attached.Count == 0, "未設定なら Controller に何も付けない (従来どおり)");
        Assert.True(Attach("http://example.com/proposals") is null && attached.Count == 0, "同じ PC でない送り先なら何も付けない");
        using var port = Attach("http://127.0.0.1:8765/proposals");
        Assert.True(port is not null && attached.Count == 1 && ReferenceEquals(attached[0], port), "使える送り先なら、その口を 1 回だけ付ける");
    }
}
