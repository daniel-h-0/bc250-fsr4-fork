#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Apply the BC250 UI integration to the pinned OptiScaler Client source."""

import argparse
import difflib
import shutil
from pathlib import Path


def prepare(source, integration):
    shutil.copy2(integration / "packages.lock.json", source / "packages.lock.json")
    originals = {}

    def edit(name, old, new):
        path = source / name
        text = path.read_text()
        originals.setdefault(name, text)
        if text.count(old) != 1:
            raise ValueError("Pinned source does not match: " + name)
        path.write_text(text.replace(old, new))

    edit(
        "Services/AppPaths.cs",
        'Path.Combine(appData, "OptiscalerClient")',
        'Path.Combine(appData, "OptiscalerClient-BC250")',
    )
    edit(
        "OptiscalerClient.csproj",
        "<TargetFramework>net10.0</TargetFramework>",
        "<TargetFramework>net10.0</TargetFramework>\n    <RestorePackagesWithLockFile>true</RestorePackagesWithLockFile>",
    )
    edit(
        "OptiscalerClient.csproj",
        '<PackageReference Include="Avalonia" Version="11.3.12" />',
        '<PackageReference Include="Tmds.DBus.Protocol" Version="0.21.3" />\n    <PackageReference Include="Avalonia" Version="11.3.12" />',
    )
    edit(
        "App.axaml.cs",
        "desktop.MainWindow = new Views.MainWindow();",
        "desktop.MainWindow = new Views.Bc250StartupWindow(() => { desktop.MainWindow = new Views.MainWindow(); desktop.MainWindow.Show(); });",
    )
    edit(
        "App.axaml.cs",
        'Assembly.GetExecutingAssembly().GetName().Version?.ToString() ?? "0.0.0"',
        '"1.0.7-bc250.5"',
    )
    edit(
        "Views/MainWindow.axaml",
        'Title="{DynamicResource TxtAppTitle}"',
        'Title="BC250 FSR4 — OptiScaler Client"',
    )
    edit(
        "Views/MainWindow.axaml.cs",
        "var welcome = new WelcomeWindow(this);\n                    await welcome.ShowDialog(this);",
        'var welcome = new ConfirmDialog(this, "BC250 FSR4 — OptiScaler Client", '
        '"Scan your library, then choose Install across your games. RC11 and its FFX/INT8 preset are included. " + '
        '"This project build uses OptiScaler Client by Agustín Montaña and contributors; licenses and complete source are included.", isAlert: true);\n'
        "                    await welcome.ShowDialog<bool>(this);",
    )
    edit(
        "Services/AppUpdateService.cs",
        "            IsError = false;\n            try",
        '            IsError = false;\n            if (System.IO.Directory.Exists(System.IO.Path.Combine(AppContext.BaseDirectory, "bc250"))) return false;\n            try',
    )
    edit(
        "Views/MainWindow.axaml",
        'Text="{DynamicResource TxtBtnBulkInstall}"',
        'Text="Install across your games"',
    )
    edit(
        "Views/MainWindow.axaml.cs",
        "new BulkInstallWindow(_componentService, installService, _games.ToList(), owner: this)",
        "new Bc250Window(_games.ToList())",
    )
    edit(
        "Views/MainWindow.axaml.cs",
        "new ManageGameWindow(this, selectedGame)",
        "new Bc250Window(new[] { selectedGame })",
    )
    # Every library install/restore entry goes through the same BC250 transaction owner.
    path = source / "Views/MainWindow.axaml.cs"
    text = path.read_text()
    start = text.index("        private async void BtnFastInstall_Click(")
    end = text.index("\n        private ", start + 1)
    old = text[start:end]
    edit(
        "Views/MainWindow.axaml.cs",
        old,
        "        private async void BtnFastInstall_Click(object? sender, RoutedEventArgs e)\n"
        "        {\n            BtnManage_Click(sender!, e);\n            await Task.CompletedTask;\n        }\n",
    )
    text = path.read_text()
    start = text.index("        private void UpdateFastInstallButton(")
    end = text.index("\n        private ", start + 1)
    edit(
        "Views/MainWindow.axaml.cs",
        text[start:end],
        "        private void UpdateFastInstallButton(Button button, Game game)\n"
        '        {\n            button.Content = "BC250 FSR4";\n        }\n',
    )
    for path in (integration / "src").glob("*.cs"):
        folder = "Views" if path.name.endswith("Window.cs") else "Services"
        shutil.copy2(path, source / folder / path.name)
    patch = "".join(
        "".join(
            difflib.unified_diff(
                before.splitlines(True),
                (source / name).read_text().splitlines(True),
                fromfile="a/" + name,
                tofile="b/" + name,
            )
        )
        for name, before in originals.items()
    )
    (source / "bc250-integration.patch").write_text(patch)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    prepare(args.source, Path(__file__).resolve().parent)
