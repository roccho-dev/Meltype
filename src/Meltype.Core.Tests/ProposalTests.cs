// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 Yukishiro

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

        public void Request(ProposalQuery query, Action<ProposalResponse> receive)
        {
            Requests.Add(query);
            _receivers[query.Generation] = receive;
        }

        public void Cancel(long generation) => Cancelled.Add(generation);
        public void Selected(ProposalSelection selection) => Selections.Add(selection);
        public void Dismissed(long generation) => Dismissals.Add(generation);

        public void Reply(ProposalQuery query, params ProposalCandidate[] candidates) =>
            _receivers[query.Generation](new ProposalResponse(query.Generation, query.Raw, candidates));
    }

    private static ProposalCandidate Candidate(string id, string representation, string meaning, string evidence) =>
        new(id, representation, meaning, evidence);

    [Test]
    private static void FirstCharacterKeepsOpaqueIdentityAndAppliesOnce()
    {
        var keyboard = new CompositionTests.Keyboard();
        var port = new FakePort();
        keyboard.Controller.AttachProposals(port);

        keyboard.Type("u");
        Assert.Equal(1, port.Requests.Count, "1文字からProposal Queryが出る");
        var query = port.Requests.Single();
        port.Reply(query,
            Candidate("meaning-a", "同じ表示", "意味A", "根拠A"),
            Candidate("meaning-b", "同じ表示", "意味B", "根拠B"));

        Assert.Equal(2, keyboard.Host.View?.Candidates.Count ?? 0, "同文異義を同じ候補面に保持する");
        keyboard.Controller.SelectCandidate(1);
        Assert.Equal("意味B", keyboard.Host.View?.Meaning, "表示文字ではなく選択した意味を読める");

        keyboard.Press(VirtualKeys.Return);
        Assert.Equal("同じ表示", keyboard.Host.Document, "選択したrepresentationを一度だけWorkingへ反映する");
        Assert.Equal(1, port.Selections.Count, "選択通知は一度だけ");
        Assert.Equal("meaning-b", port.Selections.Single().Candidate.Id, "元opaque IDを保持する");

        port.Reply(query, Candidate("meaning-a", "再送", "古い意味", "古い根拠"));
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

        port.Reply(q1, Candidate("old", "古い", "古い意味", "古い根拠"));
        Assert.True(keyboard.Host.View?.Candidates.Count is null or 0, "逆順の古い応答は表示しない");

        port.Reply(q2, Candidate("current", "現在", "現在の意味", "現在の根拠"));
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

        port.Reply(q2, Candidate("old-again", "古い再送", "古い", "古い"));
        Assert.True(keyboard.Host.View?.Candidates.Count is null or 0, "以前の同raw応答は復活しない");
        port.Reply(q4, Candidate("new", "新しい", "新しい意味", "新しい根拠"));
        Assert.Equal("新しい", keyboard.Host.View?.Candidates.Single(), "新世代だけ表示する");
    }

    [Test]
    private static void NoPortPreservesNativeComposition()
    {
        var keyboard = new CompositionTests.Keyboard();
        keyboard.Type("u");
        Assert.Equal("u", keyboard.Host.View?.Text, "portなしでは従来のraw表示");
        Assert.True(keyboard.Host.View?.Converting == false, "portなしで候補modeへ入らない");
        Assert.Equal(0, keyboard.Host.View?.Candidates.Count ?? 0, "portなしでProposal候補を作らない");
    }
}
