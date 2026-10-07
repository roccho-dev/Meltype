// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 Yukishiro

using System.Net.Http.Json;
using System.Text.Json;

namespace Meltype.Composition;

/// <summary>
/// Windows host の薄い Proposal transport。endpoint と非同期I/Oだけを持ち、意味判断・rank・資格情報を持たない。
/// </summary>
internal sealed class ProposalHttpPort : IProposalPort, IDisposable
{
    private const string EndpointVariable = "MELTYPE_PROPOSAL_URL";
    private readonly Control _invoker;
    private readonly Uri _endpoint;
    private readonly Func<bool> _targetAvailable;
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(10) };
    private CancellationTokenSource? _pending;
    private long _pendingGeneration;

    private ProposalHttpPort(Control invoker, Uri endpoint, Func<bool> targetAvailable)
    {
        _invoker = invoker;
        _endpoint = endpoint;
        _targetAvailable = targetAvailable;
    }

    public static ProposalHttpPort? FromEnvironment(Control invoker, Func<bool> targetAvailable)
    {
        var value = Environment.GetEnvironmentVariable(EndpointVariable);
        if (string.IsNullOrWhiteSpace(value)) return null;
        if (!Uri.TryCreate(value, UriKind.Absolute, out var endpoint)
            || endpoint.Scheme is not ("http" or "https"))
        {
            Diagnostics.Log.Warn($"{EndpointVariable} が有効な http(s) URL ではないため Proposal を無効にします。");
            return null;
        }
        return new ProposalHttpPort(invoker, endpoint, targetAvailable);
    }

    public void Request(ProposalQuery query, Action<ProposalResponse> receive)
    {
        CancelPending();
        var cancellation = new CancellationTokenSource();
        _pending = cancellation;
        _pendingGeneration = query.Generation;
        _ = Send(query, receive, cancellation);
    }

    public void Cancel(long generation)
    {
        if (_pendingGeneration == generation) CancelPending();
    }

    public void Selected(ProposalSelection selection) { }

    public void Dismissed(long generation) { }

    private async Task Send(ProposalQuery query, Action<ProposalResponse> receive, CancellationTokenSource cancellation)
    {
        try
        {
            using var response = await _http.PostAsJsonAsync(_endpoint,
                new { generation = query.Generation, raw = query.Raw }, cancellation.Token);
            if (!response.IsSuccessStatusCode)
            {
                Deliver(query, receive, cancellation, new ProposalResponse(query.Generation, query.Raw, [], $"HTTP_{(int)response.StatusCode}"));
                return;
            }

            await using var stream = await response.Content.ReadAsStreamAsync(cancellation.Token);
            using var document = await JsonDocument.ParseAsync(stream, cancellationToken: cancellation.Token);
            var root = document.RootElement;
            if (!root.TryGetProperty("generation", out var generationElement)
                || generationElement.GetInt64() != query.Generation
                || !root.TryGetProperty("raw", out var rawElement)
                || rawElement.GetString() != query.Raw
                || !root.TryGetProperty("proposals", out var proposalsElement)
                || proposalsElement.ValueKind != JsonValueKind.Array)
            {
                Deliver(query, receive, cancellation, new ProposalResponse(query.Generation, query.Raw, [], "INVALID_RESPONSE"));
                return;
            }

            var proposals = new List<ProposalCandidate>();
            foreach (var item in proposalsElement.EnumerateArray())
            {
                if (!item.TryGetProperty("id", out var id)
                    || !item.TryGetProperty("representation", out var representation)
                    || id.ValueKind != JsonValueKind.String
                    || representation.ValueKind != JsonValueKind.String)
                {
                    Deliver(query, receive, cancellation, new ProposalResponse(query.Generation, query.Raw, [], "INVALID_RESPONSE"));
                    return;
                }
                var meaning = item.TryGetProperty("meaning", out var meaningElement) ? meaningElement.GetRawText() : "null";
                var evidence = item.TryGetProperty("evidence", out var evidenceElement) ? evidenceElement.GetRawText() : "null";
                proposals.Add(new ProposalCandidate(id.GetString() ?? "", representation.GetString() ?? "", meaning, evidence));
            }
            Deliver(query, receive, cancellation, new ProposalResponse(query.Generation, query.Raw, proposals));
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
        catch (TaskCanceledException)
        {
            Deliver(query, receive, cancellation, new ProposalResponse(query.Generation, query.Raw, [], "TIMEOUT"));
        }
        catch (JsonException)
        {
            Deliver(query, receive, cancellation, new ProposalResponse(query.Generation, query.Raw, [], "INVALID_RESPONSE"));
        }
        catch (HttpRequestException)
        {
            Deliver(query, receive, cancellation, new ProposalResponse(query.Generation, query.Raw, [], "UNAVAILABLE"));
        }
    }

    private void Deliver(ProposalQuery query, Action<ProposalResponse> receive, CancellationTokenSource cancellation, ProposalResponse response)
    {
        if (cancellation.IsCancellationRequested || !_invoker.IsHandleCreated || _invoker.IsDisposed) return;
        _invoker.BeginInvoke(() =>
        {
            if (cancellation.IsCancellationRequested) return;
            receive(_targetAvailable()
                ? response
                : new ProposalResponse(query.Generation, query.Raw, [], "TARGET_CHANGED"));
        });
    }

    private void CancelPending()
    {
        var pending = _pending;
        _pending = null;
        _pendingGeneration = 0;
        if (pending is null) return;
        pending.Cancel();
        pending.Dispose();
    }

    public void Dispose()
    {
        CancelPending();
        _http.Dispose();
    }
}
