// SPDX-License-Identifier: GPL-3.0-or-later
using System.Text.Json;
using Meltype.Composition;
using Meltype.Config;
using Meltype.Input;

namespace Meltype.Tests;

/// <summary>B's session boundary on the adopted main wire; main's facade tests remain unchanged.</summary>
internal static class DirectTtySessionTests
{
    [Test]
    public static void Notes_AreSinglePositionedAndEscaped()
    {
        const string note = "根拠: \"引用\"\\path\n次行\t\u0001";
        var view = new CompositionView("x", ["A", "B"], 1, true, "h", Notes: [null, note]);
        using var document = JsonDocument.Parse(new SessionResult(true, [], view).ToJson());
        var serialized = document.RootElement.GetProperty("view");
        Assert.Equal(1, serialized.EnumerateObject().Count(p => p.Name == "notes"));
        var notes = serialized.GetProperty("notes");
        Assert.Equal(2, notes.GetArrayLength());
        Assert.True(notes[0].ValueKind == JsonValueKind.Null, "null retains its candidate position");
        Assert.Equal(note, notes[1].GetString());
        Assert.Equal(1, serialized.GetProperty("selectedIndex").GetInt32());
        Assert.Equal(0, document.RootElement.GetProperty("commits").GetArrayLength());
    }

    [Test]
    public static void Notes_UseMainCandidateCountWithoutDuplicateWire()
    {
        // The adopted main always emits one note slot per candidate. Preserve
        // B's missing/empty/order checks under that wire, not the older omission.
        IReadOnlyList<string?>?[] cases = [null, [], ["one"], ["one", null, "outside"]];
        foreach (var source in cases)
        {
            var view = new CompositionView("x", ["A", "B"], 0, true, "h", Notes: source);
            using var document = JsonDocument.Parse(new SessionResult(true, [], view).ToJson());
            var result = document.RootElement.GetProperty("view");
            Assert.Equal(1, result.EnumerateObject().Count(p => p.Name == "notes"));
            var notes = result.GetProperty("notes");
            Assert.Equal(view.Candidates.Count, notes.GetArrayLength());
            for (var i = 0; i < view.Candidates.Count; i++)
            {
                if (source?.ElementAtOrDefault(i) is { } expected) Assert.Equal(expected, notes[i].GetString());
                else Assert.True(notes[i].ValueKind == JsonValueKind.Null, "missing note must be null");
            }
        }
    }

    [Test]
    public static void ExpectedDeleteMetadataSurvivesTheSharedWire()
    {
        var result = new SessionResult(true, [new TextEdit(2, "交換", "日本")], null);
        using var document = JsonDocument.Parse(result.ToJson());
        var edit = document.RootElement.GetProperty("commits")[0];
        Assert.Equal("日本", edit.GetProperty("expect").GetString());
        Assert.Equal(2, edit.GetProperty("deleteBefore").GetInt32());
        Assert.Equal("交換", edit.GetProperty("text").GetString());
        // Actual TTY rejection of this unsupported effect is tested separately.
    }

    [Test]
    public static void SnapshotNeverReplaysCommitOrChangesDeleteCapability()
    {
        var session = new MeltypeSession(CompositionTests.Detector, new CompositionTests.FakeConverter(),
            new CompositionOptions(), () => new Settings());
        session.CanDeleteSurrounding = false;
        session.AttachProposals(null);
        session.HandleKey('A', 'a', false, false, false, false);
        var first = session.ProposalView();
        Assert.True(first.View is not null, "snapshot retains the preedit");
        Assert.Equal(0, first.Commits.Count);
        var commit = session.HandleKey(VirtualKeys.Return, null, false, false, false, false);
        Assert.Equal(1, commit.Commits.Count);
        for (var i = 0; i < 3; i++)
        {
            var readback = session.ProposalView();
            Assert.Equal(0, readback.Commits.Count);
            Assert.True(readback.View is null, "snapshot does not resurrect preedit");
            Assert.True(!session.CanDeleteSurrounding, "read-only snapshot cannot grant editing capability");
        }
    }
}
