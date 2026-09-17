// SPDX-License-Identifier: GPL-3.0-or-later
// BC250 integration for OptiScaler Client. Copyright (c) 2026 BC250 FSR4 contributors.
using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Platform.Storage;
using OptiscalerClient.Models;
using OptiscalerClient.Services;

namespace OptiscalerClient.Views;

public sealed class Bc250Window : Window
{
    readonly Bc250RouteService service;
    readonly Bc250CacheService cache = new();
    readonly ComboBox cacheChoice = new() { ItemsSource = new[] { "Keep current cache settings", "Enable shared cache", "Disable / recover shared cache" }, SelectedIndex = 0 };
    readonly List<(Game Game, CheckBox Select, TextBox Status)> rows = new();
    readonly TextBlock releaseLabel = new() { TextWrapping = TextWrapping.Wrap };
    readonly WrapPanel actions = new() { Orientation = Orientation.Horizontal };
    bool busy;

    public Bc250Window(IEnumerable<Game> games)
    {
        NameScope.SetNameScope(this, new NameScope());
        Title = "BC250 FSR4 — Install across your games";
        Background = new SolidColorBrush(Color.Parse("#11111c"));
        Width = 1000; Height = 700; MinWidth = 650; MinHeight = 450;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        service = new();
        var root = new DockPanel { Margin = new Thickness(20) };
        var top = new StackPanel { Spacing = 10, Margin = new Thickness(0, 0, 0, 15) };
        top.Children.Add(new TextBlock { Text = "Install across your games", FontSize = 24, FontWeight = FontWeight.Bold });
        top.Children.Add(new TextBlock { Text = "Select OptiScaler-compatible games to install or update together. Check the displayed executable and close selected games first.", TextWrapping = TextWrapping.Wrap });
        top.Children.Add(releaseLabel);
        top.Children.Add(cacheChoice);
        top.Children.Add(new TextBlock { Text = "Optional shared cache (Mesa/Linux). Close the selected games and their launcher before changing cache settings. Existing shader caches are retained.", TextWrapping = TextWrapping.Wrap });
        var import = new Button { Content = "Import DLL ZIP" };
        import.Click += async (_, _) =>
        {
            busy = true; actions.IsEnabled = false;
            try
            {
                var chosen = await StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions {
                    Title = "Choose the BC250 FSR4 DLL ZIP", AllowMultiple = false,
                    FileTypeFilter = new[] { new FilePickerFileType("DLL release ZIP") { Patterns = new[] { "*.zip" } } }
                });
                if (chosen.Count == 0) return;
                await Task.Run(() => service.Import(chosen[0].TryGetLocalPath() ?? throw new IOException("Choose a local ZIP file.")));
                ShowRelease();
            }
            catch (Exception ex) { releaseLabel.Text = ex.Message; }
            finally { busy = false; actions.IsEnabled = true; }
        };
        var select = new Button { Content = "Select available" };
        select.Click += (_, _) => { foreach (var row in rows) if (row.Select.IsEnabled) row.Select.IsChecked = true; };
        var clear = new Button { Content = "Clear selection" };
        clear.Click += (_, _) => { foreach (var row in rows) row.Select.IsChecked = false; };
        var install = new Button { Content = "Install / update selected" };
        install.Click += async (_, _) => await Run(false);
        var restore = new Button { Content = "Restore / recover selected" };
        restore.Click += async (_, _) => await Run(true);
        var cacheApply = new Button { Content = "Apply cache choice" };
        cacheApply.Click += async (_, _) => await RunCache(cacheChoice.SelectedIndex == 1 ? "enable" : cacheChoice.SelectedIndex == 2 ? "disable" : "status");
        var cacheStatus = new Button { Content = "Cache status" };
        cacheStatus.Click += async (_, _) => await RunCache("status");
        var cacheManual = new Button { Content = "Prepare manual cache" };
        cacheManual.Click += async (_, _) => await RunCache("manual");
        foreach (var control in new Control[] { import, select, clear, install, restore, cacheApply, cacheStatus, cacheManual })
        { control.Margin = new Thickness(0, 0, 10, 8); actions.Children.Add(control); }
        top.Children.Add(actions);
        DockPanel.SetDock(top, Dock.Top); root.Children.Add(top);
        var footer = new TextBlock {
            Text = "Fresh Linux setup: merge the displayed loading settings with existing launch options; keep one %command%. Heroic uses environment variables and game arguments in separate fields. Existing working OptiScaler installs keep their launch settings.",
            TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 15, 0, 0)
        };
        DockPanel.SetDock(footer, Dock.Bottom); root.Children.Add(footer);
        var list = new StackPanel { Spacing = 12 };
        foreach (var game in games.OrderBy(g => g.Name))
        {
            var check = new CheckBox { Content = game.Name };
            var status = new TextBox { IsReadOnly = true, TextWrapping = TextWrapping.Wrap, AcceptsReturn = true, BorderThickness = new Thickness(0) };
            try { status.Text = service.Describe(game).Hint; }
            catch (Exception ex) { status.Text = ex.Message; check.IsEnabled = Directory.Exists(game.InstallPath) || service.HasRecord(game); }
            var panel = new StackPanel { Spacing = 3 }; panel.Children.Add(check); panel.Children.Add(status);
            list.Children.Add(panel); rows.Add((game, check, status));
        }
        root.Children.Add(new ScrollViewer { Content = list }); Content = root;
        Closing += (_, e) => { if (busy) e.Cancel = true; };
        if (service.CurrentRelease() == null)
        {
            var bundle = Directory.GetFiles(Path.Combine(AppContext.BaseDirectory, "bc250"), "bc250-fsr4-dll-*.zip").SingleOrDefault();
            if (bundle != null) service.Import(bundle);
        }
        ShowRelease();
    }
    void ShowRelease()
    {
        var release = service.CurrentRelease();
        releaseLabel.Text = release == null ? "Choose a release ZIP to begin." : "Selected release: " + release.Version;
    }
    async Task Run(bool restore)
    {
        var selected = rows.Where(r => r.Select.IsChecked == true).ToList();
        if (selected.Count == 0) { releaseLabel.Text = "Select at least one game."; return; }
        if (restore && !await new ConfirmDialog(this, "Restore selected games",
            "Restore the files saved before BC250 installation for " + selected.Count + " selected game(s)? Client-managed cache launch settings will also be restored. Close the launcher first. Later file changes are preserved for review.").ShowDialog<bool>(this)) return;
        busy = true; actions.IsEnabled = false; cacheChoice.IsEnabled = false;
        var choice = cacheChoice.SelectedIndex;
        foreach (var row in rows) row.Select.IsEnabled = false;
        try
        {
            foreach (var row in selected)
            {
                row.Status.Text = restore ? "Restoring…" : "Installing / updating…";
                var cacheResult = "";
                try
                {
                    if (restore && cache.HasRecords) cacheResult = await Task.Run(() => cache.Run(row.Game, "disable"));
                    row.Status.Text = await Task.Run(() => restore ? service.Restore(row.Game) : service.Install(row.Game));
                    if (!restore && choice != 0)
                    {
                        try { cacheResult = await Task.Run(() => cache.Run(row.Game, choice == 1 ? "enable" : "disable")); }
                        catch (Exception ex) { cacheResult = "DLL setup completed; cache needs attention: " + ex.Message; }
                    }
                    if (cacheResult != "") row.Status.Text += "\n" + cacheResult;
                }
                catch (Exception ex) { row.Status.Text = (cacheResult == "" ? "" : cacheResult + "\n") + "Needs attention: " + ex.Message; }
            }
        }
        finally
        {
            busy = false; actions.IsEnabled = true; cacheChoice.IsEnabled = true;
            foreach (var row in rows)
                try { service.Describe(row.Game); row.Select.IsEnabled = true; }
                catch { row.Select.IsEnabled = Directory.Exists(row.Game.InstallPath) || service.HasRecord(row.Game); }
        }
    }
    async Task RunCache(string action)
    {
        var selected = rows.Where(r => r.Select.IsChecked == true).ToList();
        if (selected.Count == 0) { releaseLabel.Text = "Select at least one game."; return; }
        busy = true; actions.IsEnabled = false; cacheChoice.IsEnabled = false;
        foreach (var row in rows) row.Select.IsEnabled = false;
        try
        {
            foreach (var row in selected)
            {
                row.Status.Text = "Checking shared cache…";
                try { row.Status.Text = await Task.Run(() => cache.Run(row.Game, action)); }
                catch (Exception ex) { row.Status.Text = "Cache needs attention: " + ex.Message; }
            }
        }
        finally
        {
            busy = false; actions.IsEnabled = true; cacheChoice.IsEnabled = true;
            foreach (var row in rows) row.Select.IsEnabled = Directory.Exists(row.Game.InstallPath) || service.HasRecord(row.Game);
        }
    }

}
