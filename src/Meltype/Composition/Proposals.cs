// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 Yukishiro

using System.Net.Http.Json;
using System.Text.Json;
using Meltype.Input;

namespace Meltype.Composition;

internal readonly record struct ProposalTarget(
    IntPtr Foreground,
    Rectangle? Bounds,
    string Description,
    string Name,
    string ClassName)
{
    public static ProposalTarget? Capture(FocusInspector focus)
    {
        if (!focus.CanCapture) return null;
        var foreground = Native.GetForegroundWindow();
        if (foreground == IntPtr.Zero || ForegroundTracker.IsOwnWindow(foreground)) return null;
        var info = focus.Current;
        if (!info.IsTextInput || info.IsPassword) return null;
        return new ProposalTarget(foreground, info.Bounds, info.Description, info.Name, info.ClassName);
    }
}

internal sealed class ProposalTargetLease
{
    private long _generation;
    private ProposalTarget? _target;

    internal int CountForTest => _target is null ? 0 : 1;
    internal long GenerationForTest => _target is null ? 0 : _generation;

    public void Bind(long generation, ProposalTarget target)
    {
        _generation = generation;
        _target = target;
    }

    public void Complete(long generation, ProposalResponse response)
    {
        if (response.Error is not null || response.Candidates.Count == 0) Clear(generation);
    }

    public bool MatchesAndConsume(long generation, ProposalTarget? current)
    {
        var matches = _generation == generation && _target is { } expected && current is { } actual && actual == expected;
        Clear();
        return matches;
    }

    public void Clear(long generation)
    {
        if (_generation == generation) Clear();
    }

    public void Clear()
    {
        _generation = 0;
        _target = null;
    }
}

/// <summary>
/// Windows host の薄い Proposal transport。endpoint・非同期I/O・query時target照合だけを持ち、意味判断・rank・資格情報を持たない。
/// </summary>
internal sealed class ProposalHttpPort : IProposalPort, IDisposable
{
    private const string EndpointVariable = "MELTYPE_PROPOSAL_URL";
    private readonly Control _invoker;
    private readonly Uri _endpoint;
    private readonly Func<ProposalTarget?> _currentTarget;
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(10) };
    private readonly ProposalTargetLease _target = new();
    private CancellationTokenSource? _pending;
    private long _pendingGeneration;

    private ProposalHttpPort(Control invoker, Uri endpoint, Func<ProposalTarget?> currentTarget)
    {
        _invoker = invoker;
        _endpoint = endpoint;
        _currentTarget = currentTarget;
    }

    public static ProposalHttpPort? FromEnvironment(Control invoker, Func<ProposalTarget?> currentTarget)
    {
        var value = Environment.GetEnvironmentVariable(EndpointVariable);
        if (string.IsNullOrWhiteSpace(value)) return null;
        if (!Uri.TryCreate(value, UriKind.Absolute, out var endpoint)
            || endpoint.Scheme is not ("http" or "https"))
        {
            Diagnostics.Log.Warn($"{EndpointVariable} が有効な http(s) URL ではないため Proposal を無効にします。");
            return null;
        }
        return new ProposalHttpPort(invoker, endpoint, currentTarget);
    }

    public void Request(ProposalQuery query, Action<ProposalResponse> receive)
    {
        CancelPending();
        _target.Clear();
        if (_currentTarget() is not { } target)
        {
            receive(new ProposalResponse(query.Generation, query.Raw, [], "TARGET_UNAVAILABLE"));
            return;
        }
        _target.Bind(query.Generation, target);
        var cancellation = new CancellationTokenSource();
        _pending = cancellation;
        _pendingGeneration = query.Generation;
        _ = Send(query, receive, cancellation);
    }

    public void Cancel(long generation)
    {
        _target.Clear(generation);
        if (_pendingGeneration == generation) CancelPending();
    }

    public bool CanApply(ProposalSelection selection) =>
        _target.MatchesAndConsume(selection.Generation, _currentTarget());

    public void Selected(ProposalSelection selection) { }

    public void Dismissed(long generation)
    {
        Cancel(generation);
    }

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
            Deliver(query, receive, cancellation, ParseResponse(query, document.RootElement));
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
        catch (TaskCanceledException)
        {
            Deliver(query, receive, cancellation, new ProposalResponse(query.Generation, query.Raw, [], "TIMEOUT"));
        }
        catch (Exception ex) when (ex is JsonException or InvalidOperationException or FormatException)
        {
            Deliver(query, receive, cancellation, new ProposalResponse(query.Generation, query.Raw, [], "INVALID_RESPONSE"));
        }
        catch (HttpRequestException)
        {
            Deliver(query, receive, cancellation, new ProposalResponse(query.Generation, query.Raw, [], "UNAVAILABLE"));
        }
    }

    internal static ProposalResponse ParseResponseForTest(ProposalQuery query, JsonElement root) => ParseResponse(query, root);

    private static ProposalResponse ParseResponse(ProposalQuery query, JsonElement root)
    {
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("generation", out var generationElement)
            || generationElement.ValueKind != JsonValueKind.Number
            || !generationElement.TryGetInt64(out var generation)
            || generation != query.Generation
            || !root.TryGetProperty("raw", out var rawElement)
            || rawElement.ValueKind != JsonValueKind.String
            || rawElement.GetString() != query.Raw
            || !root.TryGetProperty("proposals", out var proposalsElement)
            || proposalsElement.ValueKind != JsonValueKind.Array)
            return new ProposalResponse(query.Generation, query.Raw, [], "INVALID_RESPONSE");

        var proposals = new List<ProposalCandidate>();
        foreach (var item in proposalsElement.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.Object
                || !item.TryGetProperty("id", out var id)
                || id.ValueKind != JsonValueKind.String
                || string.IsNullOrWhiteSpace(id.GetString())
                || !item.TryGetProperty("representation", out var representation)
                || representation.ValueKind != JsonValueKind.String
                || string.IsNullOrEmpty(representation.GetString())
                || !item.TryGetProperty("meaning", out var meaning)
                || !item.TryGetProperty("evidence", out var evidence))
                return new ProposalResponse(query.Generation, query.Raw, [], "INVALID_RESPONSE");

            proposals.Add(new ProposalCandidate(
                id.GetString()!,
                representation.GetString()!,
                meaning.Clone(),
                evidence.Clone()));
        }
        return new ProposalResponse(query.Generation, query.Raw, proposals);
    }

    private void Deliver(ProposalQuery query, Action<ProposalResponse> receive, CancellationTokenSource cancellation, ProposalResponse response)
    {
        if (cancellation.IsCancellationRequested || !_invoker.IsHandleCreated || _invoker.IsDisposed) return;
        _invoker.BeginInvoke(() =>
        {
            if (cancellation.IsCancellationRequested) return;
            FinishPending(query.Generation, cancellation);
            _target.Complete(query.Generation, response);
            receive(response);
        });
    }

    private void FinishPending(long generation, CancellationTokenSource cancellation)
    {
        if (_pendingGeneration != generation || !ReferenceEquals(_pending, cancellation)) return;
        _pending = null;
        _pendingGeneration = 0;
        cancellation.Dispose();
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
        _target.Clear();
        _http.Dispose();
    }
}
