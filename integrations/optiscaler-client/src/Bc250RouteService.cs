// SPDX-License-Identifier: GPL-3.0-or-later
// BC250 integration for OptiScaler Client. Copyright (c) 2026 BC250 FSR4 contributors.
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using OptiscalerClient.Models;
using static OptiscalerClient.Services.Bc250Transaction;

namespace OptiscalerClient.Services;

public sealed class Bc250RouteService
{
    public const string DllName = "amd_fidelityfx_upscaler_dx12.dll";
    public sealed record Release(string Version, string Hash, string File);
    public sealed record OwnedFile(string Path, string? Original, string Installed, string Backup);
    public sealed record Receipt(int Schema, string Root, string Target, string Release, List<OwnedFile> Files, string Hint = "");
    public sealed record Recipe(string Exe, string Proxy, string Input, string Arguments,
        Dictionary<string, Dictionary<string, string>> Settings);
    public sealed record Payload(string ProxyHash, Dictionary<string, string> Files, List<Recipe> Recipes);
    public sealed record Plan(string Root, string Target, Recipe? Recipe, bool Existing, string Hint);
    readonly string payloadDir;
    readonly string stateRoot;
    readonly Payload payload;
    readonly Action<string> busyCheck;

    public Bc250RouteService(string? payloadPath = null, string? statePath = null, Action<string>? checkBusy = null)
    {
        payloadDir = payloadPath ?? Path.Combine(AppPaths.GetAppDataRoot(), "BC250", "payload");
        stateRoot = statePath ?? Path.Combine(AppPaths.GetAppDataRoot(), "BC250");
        Directory.CreateDirectory(stateRoot);
        payload = JsonSerializer.Deserialize<Payload>(File.ReadAllText(Path.Combine(payloadDir, "payload.json")))
            ?? throw new IOException("Missing BC250 installation payload.");
        busyCheck = checkBusy ?? CheckBusy;
    }

    string State(Game game)
    {
        var key = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(Path.GetFullPath(game.InstallPath)))).ToLowerInvariant();
        return Path.Combine(stateRoot, "games", key);
    }
    FileStream Lock()
    {
        try { return new FileStream(Path.Combine(stateRoot, "operation.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
        catch (IOException) { throw new IOException("Another BC250 operation is running. Try again when it finishes."); }
    }
    public Receipt? ReadReceipt(Game game)
    {
        var file = Path.Combine(State(game), "receipt.json");
        if (!File.Exists(file)) return null;
        var receipt = JsonSerializer.Deserialize<Receipt>(File.ReadAllText(file));
        var root = Path.GetFullPath(game.InstallPath);
        if (receipt == null || receipt.Schema != 1 ||
            (receipt.Root != root && !receipt.Root.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.Ordinal)))
            throw new IOException("Unsupported installation record; keep the backups.");
        return receipt;
    }
    public bool HasRecord(Game game) => File.Exists(Path.Combine(State(game), "receipt.json")) || File.Exists(Path.Combine(State(game), "pending.json"));
    public Release? CurrentRelease()
    {
        var path = Path.Combine(stateRoot, "release.json");
        return File.Exists(path) ? JsonSerializer.Deserialize<Release>(File.ReadAllText(path)) : null;
    }

    public Release Import(string zipPath)
    {
        using var held = Lock();
        using var zip = ZipFile.OpenRead(zipPath);
        if (zip.Entries.Count(e => e.FullName == DllName) != 1 || zip.Entries.Count(e => e.FullName == "SHA256SUMS") != 1)
            throw new IOException("Choose this project's DLL ZIP, including its SHA256SUMS.");
        var entry = zip.GetEntry(DllName)!;
        if (entry.Length < 1024 || entry.Length > 256L * 1024 * 1024) throw new IOException("Unexpected DLL size.");
        using var checks = new StreamReader(zip.GetEntry("SHA256SUMS")!.Open());
        var match = Regex.Match(checks.ReadToEnd(), @"(?m)^([a-fA-F0-9]{64})\s+\*?" + Regex.Escape(DllName) + @"\s*$");
        if (!match.Success) throw new IOException("The ZIP has no checksum for its DLL.");
        var version = Regex.Match(Path.GetFileName(zipPath), @"^bc250-fsr4-dll-(4\.[A-Za-z0-9.-]+)\.zip$");
        if (!version.Success) throw new IOException("Keep the original bc250-fsr4-dll-4.…zip filename.");
        var hash = match.Groups[1].Value.ToLowerInvariant();
        var dir = Path.Combine(stateRoot, "releases", hash);
        Directory.CreateDirectory(dir);
        var target = Path.Combine(dir, DllName);
        var temp = Path.Combine(dir, "import.tmp");
        try
        {
            using (var input = entry.Open()) using (var output = File.Create(temp)) input.CopyTo(output);
            if (Hash(temp) != hash) throw new IOException("DLL checksum mismatch; download the ZIP again.");
            using (var pe = File.OpenRead(temp))
                if (pe.ReadByte() != 'M' || pe.ReadByte() != 'Z') throw new IOException("The file is not a Windows DLL.");
            File.Move(temp, target, true);
            var release = new Release(version.Groups[1].Value, hash, target);
            WriteText(Path.Combine(stateRoot, "release.json"), JsonSerializer.Serialize(release, Json));
            return release;
        }
        finally { if (File.Exists(temp)) File.Delete(temp); }
    }

    public Plan Describe(Game game)
    {
        var receipt = ReadReceipt(game);
        if (receipt != null)
        {
            CheckProxy(receipt.Root);
            if (ResolveTarget(receipt.Root) != receipt.Target)
                throw new IOException("The configured DLL location changed. Review this installation before updating.");
            return new(receipt.Root, receipt.Target, null, true, "Installed " + receipt.Release + ". " + receipt.Hint);
        }
        var root = Path.GetFullPath(game.InstallPath);
        var recipe = payload.Recipes.FirstOrDefault(r => File.Exists(SafePath(root, r.Exe)));
        var dir = recipe != null ? Path.GetDirectoryName(SafePath(root, recipe.Exe))! :
            new GameInstallationService().DetermineInstallDirectory(game);
        if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir)) throw new IOException("Locate the game executable using Manual install and game recipes.");
        dir = Path.GetFullPath(dir);
        if (dir != root && !dir.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.Ordinal))
            throw new IOException("The executable is outside the selected game folder.");
        var ini = Path.Combine(dir, "OptiScaler.ini");
        if (File.Exists(ini))
        {
            CheckProxy(dir);
            return new(dir, ResolveTarget(dir), recipe, true, "Existing OptiScaler; keep working launch options and the game's upscaler input.");
        }
        if (recipe == null) throw new IOException("Set up OptiScaler with this game's recipe first, then rescan to manage its FSR4 DLL here.");
        var proxy = SafePath(dir, recipe.Proxy);
        if (File.Exists(proxy)) throw new IOException("Another file uses " + recipe.Proxy + "; follow the game's mod-chaining instructions first.");
        return new(dir, Path.Combine("OptiScaler", DllName), recipe, false,
            "After install: choose " + recipe.Input + ". Steam loading: " + Launch(recipe));
    }
    void CheckProxy(string dir)
    {
        var proxies = new[] { "winmm.dll", "dxgi.dll", "version.dll", "winhttp.dll", "dbghelp.dll", "OptiScaler.dll" };
        if (!proxies.Any(p => File.Exists(Path.Combine(dir, p)) && Hash(Path.Combine(dir, p)) == payload.ProxyHash))
            throw new IOException("This OptiScaler version needs the manual route; the client build supports 10.0.0-pre1 (September 4).");
    }
    static string ResolveTarget(string dir)
    {
        var text = File.ReadAllText(Path.Combine(dir, "OptiScaler.ini"));
        var library = Get(text, "Libraries", "FfxDx12SRPath");
        var opti = Get(text, "Libraries", "OptiDllPath");
        var relative = string.IsNullOrEmpty(library) || library == "auto" ?
            Path.Combine(string.IsNullOrEmpty(opti) || opti == "auto" ? "OptiScaler" : opti.Replace('\\', '/'), DllName) : library.Replace('\\', '/');
        if (relative.Contains(':') || Path.IsPathRooted(relative))
            throw new IOException("This game uses a shared/custom absolute DLL path. Keep that setup with the manual route.");
        if (Directory.Exists(SafePathDirectory(dir, relative))) relative = Path.Combine(relative, DllName);
        var target = SafePath(dir, relative);
        if (!File.Exists(target)) throw new IOException("The configured upscaler DLL was not found; check the existing installation first.");
        return Path.GetRelativePath(dir, target);
    }
    static string SafePathDirectory(string root, string relative)
    {
        // Check parents using a non-existent filename, allowing a configured directory.
        var candidate = Path.GetFullPath(Path.Combine(root, relative));
        if (Directory.Exists(candidate)) SafePath(root, Path.Combine(relative, ".bc250-path-check"));
        return candidate;
    }
    public static string Launch(Recipe r) => (r.Exe == "Binaries/NMS.exe" ? "VKD3D_DISABLE_EXTENSIONS=\"VK_NVX_binary_import,VK_NVX_image_view_handle\" " : "") +
        "WINEDLLOVERRIDES=\"" + Path.GetFileNameWithoutExtension(r.Proxy) + "=n,b\" %command%" +
        (string.IsNullOrEmpty(r.Arguments) ? "" : " " + r.Arguments);

    public string Install(Game game)
    {
        using var held = Lock();
        var release = CurrentRelease() ?? throw new IOException("Import the BC250 DLL ZIP first.");
        if (Hash(release.File) != release.Hash) throw new IOException("The imported DLL changed; import the ZIP again.");
        var plan = Describe(game);
        busyCheck(plan.Root);
        var state = State(game);
        Directory.CreateDirectory(state);
        if (File.Exists(Path.Combine(state, "pending.json"))) throw new IOException("Use Restore / recover for the interrupted operation first.");
        var previous = ReadReceipt(game);
        var sources = new Dictionary<string, string?>();
        var temp = Path.Combine(state, "prepared");
        Directory.CreateDirectory(temp);
        if (previous == null)
        {
            if (!plan.Existing)
            {
                foreach (var (file, hash) in payload.Files)
                {
                    var source = SafePath(payloadDir, file);
                    if (Hash(source) != hash) throw new IOException("Client payload changed: " + file);
                    var destination = file == "OptiScaler.dll" ? plan.Recipe!.Proxy : file;
                    if (file == "OptiScaler.ini") continue;
                    if (file == "nvngx_dlss.dll" && File.Exists(SafePath(plan.Root, destination))) continue;
                    sources[destination] = source;
                }
            }
            var ini = Path.Combine(plan.Root, "OptiScaler.ini");
            var text = File.ReadAllText(plan.Existing ? ini : Path.Combine(payloadDir, "OptiScaler.ini"));
            var settings = new Dictionary<string, Dictionary<string, string>> {
                ["Upscalers"] = new() { ["Dx12Upscaler"] = "ffx", ["Dx11Upscaler"] = "ffx_12", ["VulkanUpscaler"] = "ffx_12" },
                ["FSR"] = new() { ["UpscalerIndex"] = "0", ["Fsr4ForceModel"] = "2", ["FsrNonLinearColorSpace"] = "false", ["Fsr4EnableWatermark"] = "auto" },
                ["FrameGen"] = new() { ["Enabled"] = "false" }
            };
            if (!plan.Existing)
            {
                settings["Plugins"] = new() { ["LoadAsiPlugins"] = "true" };
                foreach (var (section, keys) in plan.Recipe!.Settings)
                    foreach (var (key, value) in keys)
                    {
                        if (!settings.ContainsKey(section)) settings[section] = new();
                        settings[section][key] = value;
                    }
            }
            foreach (var (section, keys) in settings) foreach (var (key, value) in keys) text = Set(text, section, key, value);
            var preparedIni = Path.Combine(temp, "OptiScaler.ini");
            File.WriteAllText(preparedIni, text);
            sources["OptiScaler.ini"] = preparedIni;
        }
        sources[plan.Target] = release.File;
        var files = previous?.Files.ToList() ?? new List<OwnedFile>();
        foreach (var (relative, source) in sources)
        {
            var target = SafePath(plan.Root, relative);
            var hash = Hash(source!);
            var old = files.FirstOrDefault(f => f.Path == relative);
            if (old != null && ExistingHash(target) != old.Installed)
                throw new IOException("This file changed outside the client; keep it and review before updating: " + relative);
            if (old == null)
            {
                var original = ExistingHash(target);
                var backup = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(relative))).ToLowerInvariant();
                if (original != null)
                {
                    var backupPath = Path.Combine(state, "originals", backup);
                    Copy(target, backupPath);
                    if (Hash(backupPath) != original) throw new IOException("Backup verification failed: " + relative);
                }
                files.Add(new(relative, original, hash, backup));
            }
            else { files.Remove(old); files.Add(old with { Installed = hash }); }
        }
        var receipt = new Receipt(1, plan.Root, plan.Target, release.Version, files, previous?.Hint ?? plan.Hint);
        Apply(plan.Root, state, sources, JsonSerializer.Serialize(receipt, Json));
        game.IsOptiscalerInstalled = true;
        game.OptiscalerVersion = "10.0.0-pre1 (2026-09-04)";
        game.Fsr4ExtraVersion = "BC250 " + release.Version;
        return previous != null ? "Updated to " + release.Version + "; game settings preserved." :
            plan.Existing ? "Installed " + release.Version + "; keep working launch options." : "Files installed. " + plan.Hint;
    }

    public string Restore(Game game)
    {
        using var held = Lock();
        var state = State(game);
        var pendingPath = Path.Combine(state, "pending.json");
        if (File.Exists(pendingPath))
        {
            var pending = JsonSerializer.Deserialize<Pending>(File.ReadAllText(pendingPath))!;
            var gameRoot = Path.GetFullPath(game.InstallPath);
            if (pending.Root != gameRoot && !pending.Root.StartsWith(gameRoot + Path.DirectorySeparatorChar, StringComparison.Ordinal))
                throw new IOException("Recovery record belongs to a different game folder.");
            busyCheck(pending.Root); Recover(pending.Root, state);
            return "Recovered the interrupted operation; previous installation preserved.";
        }
        var receipt = ReadReceipt(game) ?? throw new IOException("No BC250 installation to restore.");
        busyCheck(receipt.Root);
        var sources = new Dictionary<string, string?>();
        foreach (var file in receipt.Files)
        {
            var target = SafePath(receipt.Root, file.Path);
            if (ExistingHash(target) != file.Installed)
                throw new IOException("This file has later changes. Preserve them and review before restoring: " + file.Path);
            string? backup = file.Original == null ? null : SafePath(Path.Combine(state, "originals"), file.Backup);
            if (backup != null && Hash(backup) != file.Original) throw new IOException("Original backup changed: " + file.Path);
            sources[file.Path] = backup;
        }
        Apply(receipt.Root, state, sources, null);
        foreach (var dir in receipt.Files.Select(f => Path.GetDirectoryName(SafePath(receipt.Root, f.Path))!)
            .Distinct().OrderByDescending(p => p.Length))
            if (dir != receipt.Root && Directory.Exists(dir) && !Directory.EnumerateFileSystemEntries(dir).Any()) Directory.Delete(dir);
        game.IsOptiscalerInstalled = File.Exists(Path.Combine(receipt.Root, "OptiScaler.ini"));
        game.Fsr4ExtraVersion = null;
        if (!game.IsOptiscalerInstalled) game.OptiscalerVersion = null;
        return "Restored files from before the BC250 installation. Launch settings were left unchanged.";
    }

    public static string? Get(string text, string section, string key)
    {
        var current = "";
        foreach (var line in text.Split('\n'))
        {
            var s = line.Trim();
            if (s.StartsWith('[') && s.EndsWith(']')) current = s[1..^1];
            else if (current.Equals(section, StringComparison.OrdinalIgnoreCase) && s.Contains('=') &&
                s.Split('=', 2)[0].Trim().Equals(key, StringComparison.OrdinalIgnoreCase)) return s.Split('=', 2)[1].Trim();
        }
        return null;
    }
    public static string Set(string text, string section, string key, string value)
    {
        var newline = text.Contains("\r\n") ? "\r\n" : "\n";
        var lines = text.Replace("\r\n", "\n").Split('\n').ToList();
        var inside = false; var found = false; var inserted = false;
        for (int i = 0; i < lines.Count; i++)
        {
            var s = lines[i].Trim();
            if (s.StartsWith('[') && s.EndsWith(']'))
            {
                if (inside && !inserted) { lines.Insert(i++, key + "=" + value); inserted = true; }
                inside = s[1..^1].Equals(section, StringComparison.OrdinalIgnoreCase); found |= inside;
            }
            else if (inside && s.Contains('=') && s.Split('=', 2)[0].Trim().Equals(key, StringComparison.OrdinalIgnoreCase))
            { lines[i] = key + "=" + value; inserted = true; }
        }
        if (!found) lines.Add("[" + section + "]");
        if (!inserted) lines.Add(key + "=" + value);
        return string.Join(newline, lines);
    }

    static void CheckBusy(string root)
    {
        if (!OperatingSystem.IsLinux()) throw new IOException("This client route is qualified for Linux only.");
        foreach (var dir in Directory.EnumerateDirectories("/proc").Where(d => int.TryParse(Path.GetFileName(d), out _)))
        {
            if (Path.GetFileName(dir) == Environment.ProcessId.ToString()) continue;
            try
            {
                var cmd = File.ReadAllText(Path.Combine(dir, "cmdline")).Replace('\\', '/');
                var maps = "";
                try { maps = File.ReadAllText(Path.Combine(dir, "maps")); } catch (UnauthorizedAccessException) { }
                if (cmd.Contains(root + "/", StringComparison.Ordinal) || maps.Contains(root + "/", StringComparison.Ordinal))
                    throw new InvalidOperationException("Close this game before changing its files.");
            }
            catch (IOException) { } // process exited
            catch (UnauthorizedAccessException) { } // another user's process
        }
    }
}
