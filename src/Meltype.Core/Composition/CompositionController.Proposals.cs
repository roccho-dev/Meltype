// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 roccho-dev

using Meltype.Input;

namespace Meltype.Composition;

/// <summary>任意の意味 Proposal。変換・学習の経路を増やさず、元候補の identity を選択まで保つ。</summary>
public sealed partial class CompositionController
{
    private IProposalPort? _proposalPort;
    private ProposalSession? _proposalSession;

    /// <summary>入力スレッドで取り付ける。未接続なら通常の変換をそのまま使う。</summary>
    public void AttachProposals(IProposalPort? port)
    {
        if (ReferenceEquals(_proposalPort, port)) return;
        CancelProposals();
        _proposalPort = port;
        _proposalSession = port is null ? null : new ProposalSession();
        UpdateView();
    }

    private void SyncProposals()
    {
        if (_proposalPort is null || _proposalSession is null) return;
        var change = _proposalSession.Observe(_text.IsEmpty ? "" : _text.Raw);
        if (change.CancelGeneration is { } oldGeneration) _proposalPort.Cancel(oldGeneration);
        if (change.Query is { } query) _proposalPort.Request(query, ReceiveProposal);
    }

    private void ReceiveProposal(ProposalResponse response)
    {
        if (_proposalSession is null || !_proposalSession.Receive(response, out var error)) return;
        if (error is not null) Diagnostics.Log.Warn($"Proposal を取得できませんでした: {error}");
        UpdateView();
    }

    private void CancelProposals()
    {
        if (_proposalSession?.Invalidate() is { } generation && _proposalPort is { } port) port.Cancel(generation);
    }

    private bool HandleProposalKey(int vk)
    {
        if (_proposalSession?.HasCandidates != true) return false;
        switch (vk)
        {
            case VirtualKeys.Down:
                _proposalSession.Move(+1);
                return true;
            case VirtualKeys.Up:
                _proposalSession.Move(-1);
                return true;
            case VirtualKeys.Escape:
                if (_proposalSession.Dismiss() is { } generation) _proposalPort?.Dismissed(generation);
                return true;
            case VirtualKeys.Return:
                return ApplyProposal();
            default:
                return false;
        }
    }

    private bool ApplyProposal()
    {
        if (_proposalSession?.Take(_text.Raw) is not { } selection) return false;
        var port = _proposalPort;
        var allowed = port?.CanApply(selection) == true;
        ClearComposition();
        _correctable.Clear();
        ResetContext();
        if (allowed)
        {
            _out.CommitLiteral(selection.Candidate.Representation);
            Committed?.Invoke(selection.Candidate.Representation);
            port?.Selected(selection);
        }
        UpdateView();
        return true;
    }
}
