// SPDX-License-Identifier: GPL-3.0-or-later
// BC250 integration for OptiScaler Client. Copyright (c) 2026 BC250 FSR4 contributors.
using System.Security.Cryptography;
using System.Text.Json;

namespace OptiscalerClient.Services;

// Each transaction saves its previous files and receipt before changing the game.
// A pending transaction is recoverable after interruption; originals live separately.
public sealed class Bc250Transaction
{
    public sealed record Change(string Path, string? Before, string? After, string Backup);
    public sealed record Pending(string Root, string? Receipt, List<Change> Changes, bool Committed = false);
    public static readonly JsonSerializerOptions Json = new() { WriteIndented = true };
    public static string Hash(string path)
    {
        using var file = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(file)).ToLowerInvariant();
    }
    public static string? ExistingHash(string path) => File.Exists(path) ? Hash(path) : null;

    // One spelling for a folder however it is reached (Fedora Atomic: /home/<user> is /var/home/<user>).
    // Resolves every link like realpath; a missing tail is kept as written, as Python's resolve() does.
    public static string RealPath(string path) => RealPath(path, 0);
    static string RealPath(string path, int links)
    {
        if (links > 40) throw new IOException("Too many linked folders: " + path);
        path = Path.TrimEndingDirectorySeparator(Path.GetFullPath(path));
        var parent = Path.GetDirectoryName(path);
        if (parent == null) return path;
        var resolved = Path.Combine(RealPath(parent, links), Path.GetFileName(path));
        var target = new FileInfo(resolved).LinkTarget;
        return target == null ? resolved : RealPath(Path.Combine(Path.GetDirectoryName(resolved)!, target), links + 1);
    }
    public static bool IsSameOrInside(string path, string folder)
    {
        path = RealPath(path); folder = RealPath(folder);
        return path == folder || path.StartsWith(folder.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.Ordinal);
    }

    public static string SafePath(string root, string relative)
    {
        root = Path.GetFullPath(root);
        if (Path.IsPathRooted(relative) || relative.Split('/', '\\').Contains(".."))
            throw new IOException("Unsupported path: " + relative);
        var path = Path.GetFullPath(Path.Combine(root, relative.Replace('\\', Path.DirectorySeparatorChar)));
        if (!path.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.Ordinal))
            throw new IOException("Path leaves the game folder: " + relative);
        // Check the chosen folder and everything inside it. Links above it belong to the
        // system layout (Fedora Atomic/Bazzite: /home -> var/home) and cannot redirect writes.
        for (var check = path; check.Length >= root.Length; check = Path.GetDirectoryName(check)!)
            if (new FileInfo(check).LinkTarget != null || new DirectoryInfo(check).LinkTarget != null)
                throw new IOException("Use a regular game folder and files; linked path: " + check);
        if (Directory.Exists(path)) throw new IOException("Expected a file: " + path);
        return path;
    }

    public static void WriteText(string path, string content)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var tmp = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var stream = new FileStream(tmp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            using (var writer = new StreamWriter(stream, leaveOpen: true))
            {
                writer.Write(content); writer.Flush(); stream.Flush(true);
            }
            File.Move(tmp, path, true);
        }
        finally { if (File.Exists(tmp)) File.Delete(tmp); }
    }

    public static void Copy(string source, string destination)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
        var tmp = destination + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var input = File.OpenRead(source))
            using (var output = new FileStream(tmp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            { input.CopyTo(output); output.Flush(true); }
            File.Move(tmp, destination, true);
        }
        finally { if (File.Exists(tmp)) File.Delete(tmp); }
    }

    public static void Apply(string root, string state, Dictionary<string, string?> sources,
        string? newReceipt, Action<int>? afterWrite = null)
    {
        var pendingDir = Path.Combine(state, "pending");
        var pendingFile = Path.Combine(state, "pending.json");
        if (File.Exists(pendingFile)) throw new IOException("An interrupted operation needs Restore / recover first.");
        // Leftover preparation has not changed any game files yet.
        if (Directory.Exists(pendingDir)) Directory.Delete(pendingDir, true);
        Directory.CreateDirectory(pendingDir);
        var changes = new List<Change>();
        var receiptPath = Path.Combine(state, "receipt.json");
        foreach (var (relative, source) in sources)
        {
            var target = SafePath(root, relative);
            var before = ExistingHash(target);
            var after = source == null ? null : Hash(source);
            var backup = changes.Count.ToString();
            if (before != null) Copy(target, Path.Combine(pendingDir, backup));
            if (before != ExistingHash(target) ||
                (before != null && Hash(Path.Combine(pendingDir, backup)) != before))
                throw new IOException("File changed while preparing: " + relative);
            changes.Add(new(relative, before, after, backup));
        }
        var pending = new Pending(Path.GetFullPath(root), File.Exists(receiptPath) ? File.ReadAllText(receiptPath) : null, changes);
        WriteText(pendingFile, JsonSerializer.Serialize(pending, Json));
        try
        {
            var index = 0;
            foreach (var change in changes)
            {
                var target = SafePath(root, change.Path);
                if (ExistingHash(target) != change.Before)
                    throw new IOException("File changed before replacement: " + change.Path);
                if (sources[change.Path] is string source) Copy(source, target);
                else File.Delete(target);
                if (ExistingHash(target) != change.After)
                    throw new IOException("Replacement verification failed: " + change.Path);
                afterWrite?.Invoke(++index);
            }
            if (newReceipt == null) File.Delete(receiptPath);
            else WriteText(receiptPath, newReceipt);
            WriteText(pendingFile, JsonSerializer.Serialize(pending with { Committed = true }, Json));
        }
        catch
        {
            Recover(root, state);
            throw;
        }
        Cleanup(state);
    }

    public static void Recover(string root, string state)
    {
        var file = Path.Combine(state, "pending.json");
        if (!File.Exists(file)) return;
        var pending = JsonSerializer.Deserialize<Pending>(File.ReadAllText(file))
            ?? throw new IOException("Unreadable recovery record.");
        if (pending.Root != Path.GetFullPath(root)) throw new IOException("Recovery game folder changed.");
        if (pending.Committed) { Cleanup(state); return; }
        // Validate every file before restoring any of them. Preserve outside edits.
        foreach (var change in pending.Changes)
        {
            var current = ExistingHash(SafePath(root, change.Path));
            if (current != change.Before && current != change.After)
                throw new IOException("Keep both versions and review changed file: " + change.Path);
            if (change.Before != null && Hash(SafePath(Path.Combine(state, "pending"), change.Backup)) != change.Before)
                throw new IOException("Recovery backup does not match: " + change.Path);
        }
        foreach (var change in pending.Changes.AsEnumerable().Reverse())
        {
            var target = SafePath(root, change.Path);
            if (change.Before == null) File.Delete(target);
            else Copy(SafePath(Path.Combine(state, "pending"), change.Backup), target);
        }
        var receipt = Path.Combine(state, "receipt.json");
        if (pending.Receipt == null) File.Delete(receipt);
        else WriteText(receipt, pending.Receipt);
        Cleanup(state);
    }

    static void Cleanup(string state)
    {
        // Remove the record before its backups; an orphan preparation is harmless.
        File.Delete(Path.Combine(state, "pending.json"));
        var dir = Path.Combine(state, "pending");
        if (Directory.Exists(dir)) Directory.Delete(dir, true);
    }
}
