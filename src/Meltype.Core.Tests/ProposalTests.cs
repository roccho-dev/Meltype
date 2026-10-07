// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 Yukishiro

using System.Reflection;
using System.Text.Json;
using Meltype.Composition;
using Meltype.Input;

namespace Meltype.Tests;

internal static class ProposalTests
{
    private sealed class FakePort : IProposalPort
    {
        private readonly Dictionary<long, Action<ProposalResponse>> _receivers = [];
        public List<ProposalQuery> Requests { get; } = [];
        public List<long> Cancelled { get; } = [];
        public List<long> Dismissals { get; } = [];
        public List<ProposalSelection> Selections { get; } = [];
        public bool TargetMatches { get; set; } = true;
        public int CanApplyCalls { get; private set; }

        public void Request(ProposalQuery query, Action<ProposalResponse> receive)
        {
            Requests.Add(query);
            _receivers[query.Generation] = receive;
        }

        public void Cancel(long generation) => Cancelled.Add(generation);

        public bool CanApply(ProposalSelection selection)
        {
            CanApplyCalls++;
            return TargetMatches;
        }

        public void Selected(ProposalSelection selection) => Selections.Add(selection);
        public void Dismissed(long generation) => Dismissals.Add(generation);

        public void Reply(ProposalQuery query, params ProposalCandidate[] candidates) =>
            _receivers[query.Generation](new ProposalResponse(query.Generation, query.Raw, candidates));

        public void Fail(ProposalQuery query, string error) =>
            _receivers[query.Generation](new ProposalResponse(query.Generation, query.Raw, [], error));
    }

    private static JsonElement Json(string value)
    {
        using var document = JsonDocument.Parse(value);
        return document.RootElement.Clone();
    }

    private static ProposalCandidate Candidate(string id, string representation, string meaning, string evidence) =>
        new(id, representation, Json(meaning), Json(evidence));

    [Test]
    private static void FirstCharacterKeepsTypedOpaqueIdentityAndAppliesOnce()
    {
        var keyboard = new CompositionTests.Keyboard();
        var port = new FakePort();
        keyboard.Controller.AttachProposals(port);

        keyboard.Type("u");
        Assert.Equal(1, port.Requests.Count, "1文字からProposal Queryが出る");
        var query = port.Requests.Single();
        port.Reply(query,
            Candidate("meaning-a", "同じ表示", "{\"kind\":\"text\",\"value\":\"A\"}", "{\"why\":\"A\"}"),
            Candidate("meaning-b", "同じ表示", "{\"kind\":\"action\",\"name\":\"B\"}", "{\"why\":\"B\"}"));

        Assert.Equal(2, keyboard.Host.View?.Candidates.Count ?? 0, "同文異義を同じ候補面に保持する");
        keyboard.Controller.SelectCandidate(1);
        Assert.True(keyboard.Host.View?.Meaning?.Contains("\"kind\":\"action\"") == true, "typed meaningを表示へ投影する");

        keyboard.Press(VirtualKeys.Return);
        Assert.Equal("同じ表示", keyboard.Host.Document, "選択したrepresentationを一度だけWorkingへ反映する");
        Assert.Equal(1, port.Selections.Count, "選択通知は一度だけ");
        var selected = port.Selections.Single();
        Assert.Equal("meaning-b", selected.Candidate.Id, "元opaque IDを保持する");
        Assert.Equal(JsonValueKind.Object, selected.Candidate.Meaning.ValueKind, "意味のJSON型を保持する");
        Assert.Equal("action", selected.Candidate.Meaning.GetProperty("kind").GetString(), "元typed payloadを保持する");
        Assert.Equal("B", selected.Candidate.Meaning.GetProperty("name").GetString(), "表示stringから意味を逆引きしない");

        port.Reply(query, Candidate("meaning-a", "再送", "{\"kind\":\"old\"}", "{\"why\":\"old\"}"));
        Assert.Equal("同じ表示", keyboard.Host.Document, "重複callbackは二重作用しない");
        Assert.Equal(1, port.Selections.Count, "重複callbackから再選択しない");
    }

    [Test]
    private static void LatestAbaAndCancelDoNotResurrectOldCandidates()
    {
        var keyboard = new CompositionTests.Keyboard();
        var port = new FakePort();
        keyboard.Controller.AttachProposals(port);

        keyboard.Type("u");
        var q1 = port.Requests[^1];
        keyboard.Type("x");
        var q2 = port.Requests[^1];
        Assert.True(q2.Generation > q1.Generation, "入力追加で世代が進む");
        Assert.True(port.Cancelled.Contains(q1.Generation), "古いinflightを取消す");

        port.Reply(q1, Candidate("old", "古い", "{\"kind\":\"old\"}", "{}"));
        Assert.True(keyboard.Host.View?.Candidates.Count is null or 0, "逆順の古い応答は表示しない");

        port.Reply(q2, Candidate("current", "現在", "{\"kind\":\"current\"}", "{}"));
        Assert.Equal("現在", keyboard.Host.View?.Candidates.Single(), "最新応答だけ表示する");

        keyboard.Press(VirtualKeys.Escape);
        Assert.Equal("ux", keyboard.Host.View?.Text, "cancelしてもrawは保持する");
        Assert.True(keyboard.Host.View?.Candidates.Count is null or 0, "cancelした候補は消える");
        Assert.True(port.Dismissals.Contains(q2.Generation), "cancelを元世代へ結び付ける");

        keyboard.Press(VirtualKeys.Back);
        keyboard.Type("x");
        var q4 = port.Requests[^1];
        Assert.Equal("ux", q4.Raw, "ABAで同じrawに戻れる");
        Assert.True(q4.Generation > q2.Generation, "同じrawでも新しい世代になる");

        port.Reply(q2, Candidate("old-again", "古い再送", "{\"kind\":\"old\"}", "{}"));
        Assert.True(keyboard.Host.View?.Candidates.Count is null or 0, "以前の同raw応答は復活しない");
        port.Reply(q4, Candidate("new", "新しい", "{\"kind\":\"new\"}", "{}"));
        Assert.Equal("新しい", keyboard.Host.View?.Candidates.Single(), "新世代だけ表示する");
    }

    [Test]
    private static void SameWindowDifferentFieldRefusesEffect()
    {
        var keyboard = new CompositionTests.Keyboard();
        var port = new FakePort();
        keyboard.Controller.AttachProposals(port);

        keyboard.Type("u");
        var query = port.Requests.Single();
        port.Reply(query, Candidate("candidate", "別欄へ入れてはいけない", "{\"kind\":\"text\"}", "{}"));

        // Windows portではquery時のfocused target tokenと作用直前targetを照合する。
        // ここでは同じwindow内の別fieldへ移った反例をportの不一致として再現する。
        port.TargetMatches = false;
        keyboard.Press(VirtualKeys.Return);

        Assert.Equal(1, port.CanApplyCalls, "作用直前にtargetを再照合する");
        Assert.Equal("", keyboard.Host.Document, "targetが変わったらeffect=0");
        Assert.Equal(0, port.Selections.Count, "拒否した候補を選択済みにしない");
    }

    [Test]
    private static void ProviderFailureIsDistinctFromEmptyAndHasNoProposalEffect()
    {
        var keyboard = new CompositionTests.Keyboard();
        var port = new FakePort();
        keyboard.Controller.AttachProposals(port);

        keyboard.Type("u");
        var query = port.Requests.Single();
        port.Fail(query, "INVALID_RESPONSE");

        Assert.Equal(0, keyboard.Host.View?.Candidates.Count ?? 0, "failureを候補として表示しない");
        Assert.Equal(0, port.Selections.Count, "provider failureからProposal effectを出さない");
    }

    [Test]
    private static void WindowsMalformedResponseParserIsBoundedInWindowsRunner()
    {
        if (!OperatingSystem.IsWindows()) return;
        if (!AppDomain.CurrentDomain.GetAssemblies().Any(a => a.GetName().Name == "Meltype.Tests")) return;

        var windows = Assembly.Load("Meltype");
        var type = windows.GetType("Meltype.Composition.ProposalHttpPort")
            ?? throw new AssertionException("ProposalHttpPort が見つからない");
        var method = type.GetMethod("ParseResponseForTest", BindingFlags.Static | BindingFlags.NonPublic)
            ?? throw new AssertionException("ParseResponseForTest が見つからない");

        var query = new ProposalQuery(7, "u");
        using var document = JsonDocument.Parse("{\"generation\":\"wrong-type\",\"raw\":7,\"proposals\":{}}");
        var result = (ProposalResponse?)method.Invoke(null, [query, document.RootElement]);
        Assert.Equal("INVALID_RESPONSE", result?.Error, "型不正responseはbounded failureへ閉じる");
        Assert.Equal(0, result?.Candidates.Count ?? -1, "型不正responseは候補effectを作らない");
    }

    [Test]
    private static void WindowsTargetLeaseKeepsOnlyCurrentGenerationAndClosesAllTerminalPaths()
    {
        if (!OperatingSystem.IsWindows()) return;
        if (!AppDomain.CurrentDomain.GetAssemblies().Any(a => a.GetName().Name == "Meltype.Tests")) return;

        var windows = Assembly.Load("Meltype");
        var leaseType = windows.GetType("Meltype.Composition.ProposalTargetLease")
            ?? throw new AssertionException("ProposalTargetLease が見つからない");
        var targetType = windows.GetType("Meltype.Composition.ProposalTarget")
            ?? throw new AssertionException("ProposalTarget が見つからない");
        var lease = Activator.CreateInstance(leaseType, nonPublic: true)
            ?? throw new AssertionException("ProposalTargetLease を作れない");
        var targetCtor = targetType.GetConstructors(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic).Single();
        object Target(string name) => targetCtor.Invoke([new IntPtr(1), null, $"Edit {name}", name, "Edit"]);

        var bind = leaseType.GetMethod("Bind", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)!;
        var complete = leaseType.GetMethod("Complete", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)!;
        var match = leaseType.GetMethod("MatchesAndConsume", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)!;
        var count = leaseType.GetProperty("CountForTest", BindingFlags.Instance | BindingFlags.NonPublic)!;
        var generation = leaseType.GetProperty("GenerationForTest", BindingFlags.Instance | BindingFlags.NonPublic)!;

        var first = Target("A");
        var second = Target("B");

        // 回答済み候補を選ばず次文字へ進む: latest一系統なので次世代Bindが旧targetを置換する。
        bind.Invoke(lease, [1L, first]);
        bind.Invoke(lease, [2L, second]);
        Assert.Equal(1, (int)count.GetValue(lease)!, "target leaseは常に最大1件");
        Assert.Equal(2L, (long)generation.GetValue(lease)!, "次世代だけを保持する");

        // empty/errorは選択不能なのでresponse完了時点でtargetを回収する。
        complete.Invoke(lease, [2L, new ProposalResponse(2, "ux", [], "UNAVAILABLE")]);
        Assert.Equal(0, (int)count.GetValue(lease)!, "provider errorでtargetを残さない");
        bind.Invoke(lease, [3L, first]);
        complete.Invoke(lease, [3L, new ProposalResponse(3, "uxa", [])]);
        Assert.Equal(0, (int)count.GetValue(lease)!, "empty responseでtargetを残さない");

        // 作用直前target不一致も、その場でleaseを消費してeffect拒否後に残さない。
        bind.Invoke(lease, [4L, first]);
        var allowed = (bool)match.Invoke(lease, [4L, second])!;
        Assert.True(!allowed, "別fieldへの作用を拒否する");
        Assert.Equal(0, (int)count.GetValue(lease)!, "CanApply拒否後にtargetを残さない");

        bind.Invoke(lease, [5L, first]);
        allowed = (bool)match.Invoke(lease, [5L, first])!;
        Assert.True(allowed, "同じtargetは一度だけ許可する");
        Assert.Equal(0, (int)count.GetValue(lease)!, "成功時も作用前にleaseを消費する");
    }

    [Test]
    private static void WindowsCancellationKeepsCapturedTokenValidUntilAsyncCompletion()
    {
        if (!OperatingSystem.IsWindows()) return;
        if (!AppDomain.CurrentDomain.GetAssemblies().Any(a => a.GetName().Name == "Meltype.Tests")) return;

        var windows = Assembly.Load("Meltype");
        var type = windows.GetType("Meltype.Composition.ProposalPendingRequest")
            ?? throw new AssertionException("ProposalPendingRequest が見つからない");
        var pending = Activator.CreateInstance(type, nonPublic: true)
            ?? throw new AssertionException("ProposalPendingRequest を作れない");
        var token = (CancellationToken)type.GetProperty("Token")!.GetValue(pending)!;
        var cancel = type.GetMethod("Cancel")!;
        var complete = type.GetMethod("Complete")!;
        var cancelled = type.GetProperty("IsCancelled")!;

        // 旧実装の競合: manager がcancelした後にHTTP continuationが次のawait用tokenを使う。
        // tokenは生成時に一度だけ捕捉し、cancelはsourceをdisposeしないので継続側で安全に参照できる。
        cancel.Invoke(pending, null);
        Assert.True(token.IsCancellationRequested, "cancel後も捕捉済みtokenで取消を観測できる");
        Assert.True((bool)cancelled.GetValue(pending)!, "queued UI callbackも取消を観測できる");

        // async finallyだけがsourceをdisposeする。完了後に遅いcancelが競合してもdisposed sourceへCancelしない。
        complete.Invoke(pending, null);
        cancel.Invoke(pending, null);
        complete.Invoke(pending, null);
        Assert.True((bool)cancelled.GetValue(pending)!, "cancel/complete競合をidempotentに閉じる");
    }

    [Test]
    private static void NoPortPreservesNativeComposition()
    {
        var keyboard = new CompositionTests.Keyboard();
        keyboard.Type("u");
        Assert.Equal("う", keyboard.Host.View?.Text, "portなしでは従来のnative表示");
        Assert.True(keyboard.Host.View?.Converting == false, "portなしで候補modeへ入らない");
        Assert.Equal(0, keyboard.Host.View?.Candidates.Count ?? 0, "portなしでProposal候補を作らない");
    }
}
