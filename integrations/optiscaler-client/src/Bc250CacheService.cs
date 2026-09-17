// SPDX-License-Identifier: GPL-3.0-or-later
// BC250 integration for OptiScaler Client. Copyright (c) 2026 BC250 FSR4 contributors.
using System.Diagnostics;
using System.ComponentModel;
using System.Text.Json;
using OptiscalerClient.Models;

namespace OptiscalerClient.Services;

public sealed class Bc250CacheService
{
    public string Run(Game game, string action)
    {
        var tools = Path.Combine(AppContext.BaseDirectory, "bc250", "cache-tools");
        var hashes = JsonSerializer.Deserialize<Dictionary<string, string>>(File.ReadAllText(Path.Combine(tools, "SHA256.json")))!;
        foreach (var (name, hash) in hashes)
        {
            if (Path.GetFileName(name) != name || Bc250Transaction.Hash(Path.Combine(tools, name)) != hash)
                throw new IOException("Cache tools changed; extract the client archive again.");
        }
        var start = new ProcessStartInfo("python3") {
            UseShellExecute = false, RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            CreateNoWindow = true
        };
        start.ArgumentList.Add("-B"); start.ArgumentList.Add(Path.Combine(tools, "client-cache.py"));
        Process started;
        try { started = Process.Start(start) ?? throw new IOException("Could not start the cache helper."); }
        catch (Win32Exception) { throw new IOException("Install Python 3.8 or newer to use optional shared cache setup."); }
        using var process = started;
        var output = process.StandardOutput.ReadToEndAsync();
        var errors = process.StandardError.ReadToEndAsync();
        process.StandardInput.Write(JsonSerializer.Serialize(new {
            action, state = Path.Combine(AppPaths.GetAppDataRoot(), "BC250", "cache"),
            game = new { root = game.InstallPath, appid = game.AppId, platform = game.Platform.ToString() }
        }));
        process.StandardInput.Close();
        if (!process.WaitForExit(60000))
        {
            process.Kill(entireProcessTree: true);
            throw new IOException("Cache setup timed out. Use Disable / recover to check any interrupted change.");
        }
        Task.WaitAll(output, errors);
        JsonDocument result;
        try { result = JsonDocument.Parse(output.Result); }
        catch (JsonException) { throw new IOException("Cache helper could not run. Check that Python 3.8 or newer is available."); }
        using (result)
        {
            var root = result.RootElement;
            var message = root.GetProperty("message").GetString() ?? "";
            if (process.ExitCode != 0) throw new IOException(message);
            if (action is "status" or "manual")
            {
                message += "\nShared store: " + root.GetProperty("store").GetString();
                if (action == "manual") message += "\nWrapper: " + root.GetProperty("wrapper").GetString() + "\n" + root.GetProperty("manual").GetString();
                if (root.TryGetProperty("last_launch", out var launch))
                    message += "\nLast launch preparation: " + launch.GetProperty("result").GetString() +
                        (launch.TryGetProperty("reason", out var reason) ? " — " + reason.GetString() : "") + ". This does not measure cache hits.";
            }
            return message;
        }
    }

    public bool HasRecords
    {
        get
        {
            var folder = Path.Combine(AppPaths.GetAppDataRoot(), "BC250", "cache", "games");
            return Directory.Exists(folder) && Directory.EnumerateFiles(folder, "*.json", SearchOption.AllDirectories)
                .Any(path => Path.GetFileName(path) is "receipt.json" or "pending.json");
        }
    }
}
