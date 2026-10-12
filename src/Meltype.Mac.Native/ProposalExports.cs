// SPDX-License-Identifier: GPL-3.0-or-later
// Native-only transport for the existing Core port. No ranker, HTTP or worker
// enters Core; callers deliver responses on the same thread as key processing.
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using Meltype.Composition;

namespace Meltype.Mac;

public static unsafe class ProposalExports
{
    private sealed class Exchange(MeltypeSession session) : IProposalPort
    {
        public MeltypeSession Session { get; } = session;
        public ProposalQuery? Pending;
        public ProposalQuery? Current;
        public ProposalSelection? Selection;
        private Action<ProposalResponse>? _receive;
        private string _target = "";
        private long _epoch;
        private long _queryEpoch;

        public void Target(string target)
        {
            if (target == _target) return;
            _target = target;
            _epoch++;
        }

        public void Request(ProposalQuery query, Action<ProposalResponse> receive)
        {
            Pending = Current = query;
            _receive = receive;
            _queryEpoch = _epoch;
            Selection = null;
        }

        public void Cancel(long generation)
        {
            if (Current?.Generation != generation) return;
            Current = Pending = null;
            _receive = null;
        }

        public void Dismissed(long generation) => Cancel(generation);
        public void Selected(ProposalSelection selection) => Selection = selection;

        public bool CanApply(ProposalSelection selection)
        {
            var allowed = _target.Length != 0 && _queryEpoch == _epoch &&
                Current is { } current && current.Generation == selection.Generation && current.Raw == selection.Raw &&
                !selection.Candidate.Representation.Any(char.IsControl);
            Current = Pending = null;
            _receive = null;
            return allowed;
        }

        public void Receive(long generation, string json)
        {
            if (Current is not { } query || query.Generation != generation || _receive is null) return;
            var receive = _receive;
            _receive = null;
            ProposalResponse response;
            try
            {
                using var document = JsonDocument.Parse(json, new JsonDocumentOptions { MaxDepth = 32 });
                var root = document.RootElement;
                if (root.GetProperty("generation").GetInt64() != query.Generation ||
                    root.GetProperty("raw").GetString() != query.Raw)
                    throw new FormatException();
                string? error = null;
                if (root.TryGetProperty("error", out var failure) && failure.ValueKind != JsonValueKind.Null)
                    error = failure.GetString() ?? "UNAVAILABLE";
                var candidates = new List<ProposalCandidate>();
                if (error is null)
                {
                    var array = root.GetProperty("proposals");
                    if (array.GetArrayLength() > 128) throw new FormatException();
                    foreach (var item in array.EnumerateArray())
                    {
                        var id = item.GetProperty("id").GetString();
                        var text = item.GetProperty("representation").GetString();
                        if (string.IsNullOrWhiteSpace(id) || id.Length > 1024 ||
                            string.IsNullOrEmpty(text) || text.Length > 65536)
                            throw new FormatException();
                        candidates.Add(new(id, text, item.GetProperty("meaning").Clone(), item.GetProperty("evidence").Clone()));
                    }
                }
                response = new(query.Generation, query.Raw, candidates, error);
            }
            catch (Exception ex) when (ex is JsonException or InvalidOperationException or KeyNotFoundException or FormatException or OverflowException)
            {
                response = new(query.Generation, query.Raw, [], "INVALID_RESPONSE");
            }
            if (response.Error is not null || response.Candidates.Count == 0) Current = Pending = null;
            receive(response); // Existing Core checks generation, raw, IDs, cancel and one-shot selection.
        }
    }

    private static Exchange? Get(IntPtr handle) =>
        handle == IntPtr.Zero ? null : GCHandle.FromIntPtr(handle).Target as Exchange;

    // Same UTF-8 / NativeMemory contract as the existing meltype_free export.
    private static byte* Text(string value)
    {
        var bytes = Encoding.UTF8.GetBytes(value);
        var pointer = (byte*)NativeMemory.Alloc((nuint)bytes.Length + 1);
        bytes.AsSpan().CopyTo(new Span<byte>(pointer, bytes.Length));
        pointer[bytes.Length] = 0;
        return pointer;
    }

    private static string Json(Action<Utf8JsonWriter> write)
    {
        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream)) { write(writer); }
        return Encoding.UTF8.GetString(stream.ToArray());
    }

    [UnmanagedCallersOnly(EntryPoint = "meltype_proposals_create")]
    public static IntPtr Create(IntPtr sessionHandle)
    {
        try
        {
            if (sessionHandle == IntPtr.Zero || GCHandle.FromIntPtr(sessionHandle).Target is not MeltypeSession session) return IntPtr.Zero;
            var exchange = new Exchange(session);
            session.AttachProposals(exchange);
            return GCHandle.ToIntPtr(GCHandle.Alloc(exchange));
        }
        catch { return IntPtr.Zero; }
    }

    [UnmanagedCallersOnly(EntryPoint = "meltype_proposals_destroy")]
    public static void Destroy(IntPtr handle)
    {
        if (Get(handle) is not { } exchange) return;
        exchange.Session.AttachProposals(null);
        GCHandle.FromIntPtr(handle).Free();
    }

    [UnmanagedCallersOnly(EntryPoint = "meltype_proposals_target")]
    public static void Target(IntPtr handle, byte* target)
    {
        Get(handle)?.Target(Marshal.PtrToStringUTF8((IntPtr)target) ?? "");
    }

    [UnmanagedCallersOnly(EntryPoint = "meltype_proposals_request")]
    public static byte* Request(IntPtr handle)
    {
        if (Get(handle) is not { } exchange || exchange.Pending is not { } query) return null;
        exchange.Pending = null;
        return Text(Json(w => {
            w.WriteStartObject(); w.WriteNumber("generation", query.Generation);
            w.WriteString("raw", query.Raw); w.WriteEndObject();
        }));
    }

    [UnmanagedCallersOnly(EntryPoint = "meltype_proposals_receive")]
    public static byte* Receive(IntPtr handle, long generation, byte* json)
    {
        try
        {
            if (Get(handle) is not { } exchange) return null;
            var value = Marshal.PtrToStringUTF8((IntPtr)json) ?? "";
            exchange.Receive(generation, value.Length <= 1024 * 1024 ? value : "");
            return Text(exchange.Session.ProposalView().ToJson());
        }
        catch { return null; }
    }

    [UnmanagedCallersOnly(EntryPoint = "meltype_proposals_selected")]
    public static byte* Selected(IntPtr handle)
    {
        if (Get(handle) is not { } exchange || exchange.Selection is not { } selection) return null;
        exchange.Selection = null;
        return Text(Json(w => {
            w.WriteStartObject(); w.WriteNumber("generation", selection.Generation); w.WriteString("raw", selection.Raw);
            w.WritePropertyName("proposal"); w.WriteStartObject();
            w.WriteString("id", selection.Candidate.Id); w.WriteString("representation", selection.Candidate.Representation);
            w.WritePropertyName("meaning"); selection.Candidate.Meaning.WriteTo(w);
            w.WritePropertyName("evidence"); selection.Candidate.Evidence.WriteTo(w);
            w.WriteEndObject(); w.WriteEndObject();
        }));
    }
}
