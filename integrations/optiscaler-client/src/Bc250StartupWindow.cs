// SPDX-License-Identifier: GPL-3.0-or-later
// BC250 integration for OptiScaler Client. Copyright (c) 2026 BC250 FSR4 contributors.
using System.Net.Http;
using System.Text.Json;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using OptiscalerClient.Services;

namespace OptiscalerClient.Views;

public sealed class Bc250StartupWindow : Window
{
    readonly TextBlock status = new() { TextWrapping = TextWrapping.Wrap };
    readonly Button retry = new() { Content = "Retry", IsVisible = false };
    readonly Action ready;
    public Bc250StartupWindow(Action onReady)
    {
        ready = onReady;
        Title = "BC250 FSR4 — OptiScaler Client"; Width = 550; Height = 240;
        Background = new SolidColorBrush(Color.Parse("#11111c"));
        var panel = new StackPanel { Margin = new Thickness(25), Spacing = 16 };
        panel.Children.Add(new TextBlock { Text = "Preparing OptiScaler Client", FontSize = 23 });
        panel.Children.Add(status); panel.Children.Add(retry); Content = panel;
        Opened += async (_, _) => await Prepare();
        retry.Click += async (_, _) => await Prepare();
    }
    async Task Prepare()
    {
        retry.IsVisible = false;
        var progress = new Progress<string>(message => status.Text = message);
        try
        {
            await Task.Run(() => PreparePayload(progress));
            ready(); Close();
        }
        catch (Exception ex) { status.Text = "Setup needs attention: " + ex.Message; retry.IsVisible = true; }
    }
    public static async Task PreparePayload(IProgress<string>? progress = null)
    {
        var shipped = Path.Combine(AppContext.BaseDirectory, "bc250");
        var data = Path.Combine(AppPaths.GetAppDataRoot(), "BC250");
        Directory.CreateDirectory(data);
        using var held = new FileStream(Path.Combine(data, "prepare.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        var manifestText = File.ReadAllText(Path.Combine(shipped, "payload.json"));
        var manifest = JsonSerializer.Deserialize<Bc250RouteService.Payload>(manifestText)!;
        var target = Path.Combine(data, "payload");
        if (File.Exists(Path.Combine(target, "payload.json")) && File.ReadAllText(Path.Combine(target, "payload.json")) == manifestText &&
            manifest.Files.All(f => File.Exists(Path.Combine(target, f.Key)) && Bc250Transaction.Hash(Path.Combine(target, f.Key)) == f.Value)) return;
        using var metadata = JsonDocument.Parse(File.ReadAllText(Path.Combine(shipped, "inputs.json")));
        var downloads = Path.Combine(data, "downloads"); Directory.CreateDirectory(downloads);
        var local = new Dictionary<string, string>();
        using var http = new HttpClient { Timeout = TimeSpan.FromMinutes(5) };
        http.DefaultRequestHeaders.UserAgent.ParseAdd("BC250-FSR4-OptiClient/1");
        foreach (var name in new[] { "opti", "patcher", "dlss", "dlss_license" })
        {
            var item = metadata.RootElement.GetProperty(name);
            var file = Path.Combine(downloads, item.GetProperty("file").GetString()!);
            var expected = item.GetProperty("sha256").GetString();
            progress?.Report("Downloading and checking " + Path.GetFileName(file) + " from its upstream project. This is needed once.");
            if (!File.Exists(file) || Bc250Transaction.Hash(file) != expected)
            {
                var temp = file + ".download";
                try
                {
                    using var input = await http.GetStreamAsync(item.GetProperty("url").GetString());
                    using (var output = File.Create(temp)) await input.CopyToAsync(output);
                    if (Bc250Transaction.Hash(temp) != expected) throw new IOException("Download checksum mismatch: " + Path.GetFileName(file));
                    File.Move(temp, file, true);
                }
                finally { if (File.Exists(temp)) File.Delete(temp); }
            }
            local[name] = file;
        }
        progress?.Report("Extracting and checking OptiScaler files. This is needed once.");
        var components = new ComponentManagementService();
        var version = await components.ImportCustomOptiScalerVersionAsync(local["opti"]);
        var cache = components.GetOptiScalerCachePath(version);
        Directory.CreateDirectory(target);
        foreach (var (file, hash) in manifest.Files)
        {
            var source = file switch {
                "nvngx_dlss.dll" => local["dlss"],
                "OptiScaler/plugins/OptiPatcher.asi" => local["patcher"],
                "Licenses/NVIDIA-DLSS-LICENSE.txt" => local["dlss_license"],
                _ => Bc250Transaction.SafePath(cache, file)
            };
            if (Bc250Transaction.Hash(source) != hash) throw new IOException("Unexpected dependency file: " + file);
            Bc250Transaction.Copy(source, Bc250Transaction.SafePath(target, file));
        }
        Bc250Transaction.WriteText(Path.Combine(target, "payload.json"), manifestText);
    }
}
