// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 Yukishiro

using System.Text.Json;

namespace Meltype.Composition;

/// <summary>意味 Proposal。表示文字列とは別に、元の opaque identity と typed JSON payload を保持する。</summary>
public sealed record ProposalCandidate(string Id, string Representation, JsonElement Meaning, JsonElement Evidence);

/// <summary>Controller が観測した一つの raw input 世代。</summary>
public sealed record ProposalQuery(long Generation, string Raw);

/// <summary>一つの Query に対する候補集合。Error は候補なしとは別の失敗。</summary>
public sealed record ProposalResponse(
    long Generation,
    string Raw,
    IReadOnlyList<ProposalCandidate> Candidates,
    string? Error = null);

/// <summary>Human が選んだ元 Proposal。表示文字列から逆引きしない。</summary>
public sealed record ProposalSelection(long Generation, string Raw, ProposalCandidate Candidate);

/// <summary>意味判断を Controller から分離する任意port。実装は非同期でも Controller をブロックしない。</summary>
public interface IProposalPort
{
    void Request(ProposalQuery query, Action<ProposalResponse> receive);
    void Cancel(long generation);
    bool CanApply(ProposalSelection selection);
    void Selected(ProposalSelection selection);
    void Dismissed(long generation);
}

internal readonly record struct ProposalChange(long? CancelGeneration, ProposalQuery? Query);

/// <summary>
/// raw の正本を持たず、Controller から渡された snapshot の世代・候補 identity・一度だけ選択を管理するpure状態。
/// </summary>
internal sealed class ProposalSession
{
    private long _generation;
    private string _raw = "";
    private ProposalCandidate[] _candidates = [];
    private int _selectedIndex = -1;
    private bool _answered;
    private bool _dismissed;
    private bool _consumed;

    public IReadOnlyList<ProposalCandidate> Candidates => _candidates;
    public int SelectedIndex => _selectedIndex;
    public bool HasCandidates => !_dismissed && !_consumed && _candidates.Length > 0;
    public ProposalCandidate? Selected => HasCandidates && _selectedIndex >= 0 && _selectedIndex < _candidates.Length
        ? _candidates[_selectedIndex]
        : null;

    public ProposalChange Observe(string raw)
    {
        if (raw == _raw) return new(null, null);
        long? cancel = _generation > 0 && !_answered ? _generation : null;
        _generation++;
        _raw = raw;
        _candidates = [];
        _selectedIndex = -1;
        _answered = false;
        _dismissed = false;
        _consumed = false;
        return new(cancel, raw.Length == 0 ? null : new ProposalQuery(_generation, raw));
    }

    public bool Receive(ProposalResponse response, out string? error)
    {
        error = null;
        if (response.Generation != _generation || response.Raw != _raw || _answered || _dismissed || _consumed) return false;
        _answered = true;
        if (response.Error is not null)
        {
            error = response.Error;
            _candidates = [];
            _selectedIndex = -1;
            return true;
        }
        var items = response.Candidates?.ToArray() ?? [];
        if (items.Any(candidate => string.IsNullOrWhiteSpace(candidate.Id)
                || string.IsNullOrEmpty(candidate.Representation)
                || candidate.Meaning.ValueKind == JsonValueKind.Undefined
                || candidate.Evidence.ValueKind == JsonValueKind.Undefined)
            || items.Select(candidate => candidate.Id).Distinct(StringComparer.Ordinal).Count() != items.Length)
        {
            error = "INVALID_PROPOSALS";
            _candidates = [];
            _selectedIndex = -1;
            return true;
        }
        _candidates = items;
        _selectedIndex = items.Length == 0 ? -1 : 0;
        return true;
    }

    public bool SetSelected(int index)
    {
        if (!HasCandidates || index < 0 || index >= _candidates.Length) return false;
        _selectedIndex = index;
        return true;
    }

    public void Move(int delta)
    {
        if (!HasCandidates) return;
        _selectedIndex = (_selectedIndex + delta + _candidates.Length) % _candidates.Length;
    }

    public long? Dismiss()
    {
        if (_generation == 0 || _dismissed || _consumed) return null;
        _dismissed = true;
        _candidates = [];
        _selectedIndex = -1;
        return _generation;
    }

    public ProposalSelection? Take(string currentRaw)
    {
        var candidate = Selected;
        if (candidate is null || currentRaw != _raw) return null;
        _consumed = true;
        _candidates = [];
        _selectedIndex = -1;
        var selection = new ProposalSelection(_generation, _raw, candidate);
        _raw = "";
        return selection;
    }

    public long? Invalidate()
    {
        long? cancel = _generation > 0 && !_answered ? _generation : null;
        _generation++;
        _raw = "";
        _candidates = [];
        _selectedIndex = -1;
        _answered = true;
        _dismissed = true;
        _consumed = true;
        return cancel;
    }
}
