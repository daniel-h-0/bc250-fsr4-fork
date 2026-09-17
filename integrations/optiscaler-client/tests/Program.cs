// SPDX-License-Identifier: GPL-3.0-or-later
using System.Diagnostics;
using System.IO.Compression;
using System.Text.Json;
using OptiscalerClient.Models;
using OptiscalerClient.Services;
using static OptiscalerClient.Services.Bc250Transaction;

if (args.Length > 0 && args[0] == "--crash")
{
    Apply(args[1], args[2], new() { ["one"] = args[3], ["two"] = args[3] }, "new receipt",
        count => { if (count == 1) Environment.Exit(77); });
    return;
}
if (args.Length > 0 && args[0] == "--real")
{
    var realRoot = Path.GetFullPath(args[3]); Directory.CreateDirectory(realRoot);
    var svc = new Bc250RouteService(args[1], Path.Combine(realRoot, "state"), _ => { });
    var payload = JsonSerializer.Deserialize<Bc250RouteService.Payload>(File.ReadAllText(Path.Combine(args[1], "payload.json")))!;
    svc.Import(args[2]);
    var results = new List<object>();
    foreach (var recipe in payload.Recipes)
    {
        var dir = Path.Combine(realRoot, Path.GetFileNameWithoutExtension(recipe.Exe));
        var exe = Path.Combine(dir, recipe.Exe); Directory.CreateDirectory(Path.GetDirectoryName(exe)!);
        File.Copy(args[4], exe, true);
        var game = new Game { Name = Path.GetFileNameWithoutExtension(recipe.Exe), InstallPath = dir, ExecutablePath = exe, Platform = GamePlatform.Manual };
        var result = svc.Install(game);
        var receipt = svc.ReadReceipt(game)!;
        var ini = Path.Combine(receipt.Root, "OptiScaler.ini"); var firstIni = Hash(ini);
        svc.Install(game);
        if (Hash(ini) != firstIni) throw new Exception("Update changed INI");
        results.Add(new { game.Name, game.InstallPath, game.ExecutablePath, recipe.Proxy, receipt.Root,
            Target = Path.Combine(receipt.Root, receipt.Target), Sha256 = Hash(Path.Combine(receipt.Root, receipt.Target)),
            Ini = ini, IniPreservedOnUpdate = true, Result = result });
    }
    File.WriteAllText(Path.Combine(realRoot, "results.json"), JsonSerializer.Serialize(results, Json));
    Console.WriteLine("PASS real payload installation and repeat updates for all five recipes");
    return;
}
var root = Path.Combine(Path.GetTempPath(), "bc250-client-tests-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(root);
int passed = 0;
void Check(bool ok, string name) { if (!ok) throw new Exception(name); passed++; Console.WriteLine("PASS " + name); }
void Reject(Action action, string name)
{
    try { action(); } catch (IOException) { Check(true, name); return; }
    throw new Exception("Did not reject: " + name);
}
string Make(string relative, string text)
{
    var path = Path.Combine(root, relative); Directory.CreateDirectory(Path.GetDirectoryName(path)!); File.WriteAllText(path, text); return path;
}
Game GameAt(string name) => new() { Name = name, InstallPath = Path.Combine(root, name), ExecutablePath = Path.Combine(root, name, "game.exe"), Platform = GamePlatform.Manual };
string Zip(string version, string content, bool corrupt = false)
{
    var dll = Make("zip-source", "MZ" + new string('x', 2048) + content);
    var zip = Path.Combine(root, "bc250-fsr4-dll-4.0.0-" + version + ".zip");
    using var archive = ZipFile.Open(zip, ZipArchiveMode.Create);
    archive.CreateEntryFromFile(dll, Bc250RouteService.DllName);
    using var writer = new StreamWriter(archive.CreateEntry("SHA256SUMS").Open());
    writer.Write((corrupt ? new string('0', 64) : Hash(dll)) + "  " + Bc250RouteService.DllName + "\n");
    return zip;
}
try
{
    var payload = Path.Combine(root, "payload");
    var proxy = Make("payload/OptiScaler.dll", "MZsynthetic OptiScaler");
    Make("payload/OptiScaler.ini", "[Libraries]\nOptiDllPath=auto\nFfxDx12SRPath=auto\n[FSR]\nFsr4ForceModel=auto\n");
    Make("payload/OptiScaler/" + Bc250RouteService.DllName, "MZoriginal bundled DLL");
    Make("payload/OptiScaler/plugins/OptiPatcher.asi", "MZplugin");
    Make("payload/nvngx_dlss.dll", "MZhelper");
    var manifest = new Bc250RouteService.Payload(Hash(proxy),
        Directory.GetFiles(payload, "*", SearchOption.AllDirectories).ToDictionary(f => Path.GetRelativePath(payload, f), Hash),
        new() { new("game.exe", "winmm.dll", "DLSS", "", new() { ["Spoofing"] = new() { ["Dxgi"] = "false" } }) });
    Make("payload/payload.json", JsonSerializer.Serialize(manifest));
    var state = Path.Combine(root, "state");
    var service = new Bc250RouteService(payload, state, _ => { });
    var first = service.Import(Zip("test1", "first"));
    var a = GameAt("a"); var b = GameAt("b");
    foreach (var game in new[] { a, b })
    {
        Make(game.Name + "/game.exe", "game executable");
        Make(game.Name + "/nvngx_dlss.dll", "original game helper");
        service.Install(game);
        Check(Hash(Path.Combine(game.InstallPath, "OptiScaler", Bc250RouteService.DllName)) == first.Hash, "correct nested DLL: " + game.Name);
        Check(File.ReadAllText(Path.Combine(game.InstallPath, "nvngx_dlss.dll")) == "original game helper", "existing helper preserved: " + game.Name);
        Check(Bc250RouteService.Get(File.ReadAllText(Path.Combine(game.InstallPath, "OptiScaler.ini")), "FSR", "Fsr4ForceModel") == "2", "INT8 preset: " + game.Name);
    }
    var aIni = Path.Combine(a.InstallPath, "OptiScaler.ini"); var bIni = Path.Combine(b.InstallPath, "OptiScaler.ini");
    File.AppendAllText(aIni, "\n[User]\nCustom=keep me\n");
    var editedIni = File.ReadAllBytes(aIni); var bBefore = File.ReadAllBytes(bIni);
    var second = service.Import(Zip("test2", "second"));
    foreach (var game in new[] { a, b }) service.Install(game);
    Check(new[] { a, b }.All(g => Hash(Path.Combine(g.InstallPath, "OptiScaler", Bc250RouteService.DllName)) == second.Hash), "one imported release updates both installed games");
    Check(File.ReadAllBytes(aIni).SequenceEqual(editedIni) && File.ReadAllBytes(bIni).SequenceEqual(bBefore), "update preserves edited and unchanged INIs byte-for-byte");
    File.WriteAllText(bIni, Bc250RouteService.Set(File.ReadAllText(bIni), "Libraries", "FfxDx12SRPath", "different.dll"));
    Make("b/different.dll", "another DLL");
    Reject(() => service.Install(b), "changed DLL selection held for review");
    File.WriteAllBytes(bIni, bBefore);
    File.WriteAllText(Path.Combine(b.InstallPath, "winmm.dll"), "different adapter");
    Reject(() => service.Install(b), "changed adapter held for review");
    File.Copy(proxy, Path.Combine(b.InstallPath, "winmm.dll"), true);
    Reject(() => service.Restore(a), "restore refuses later user edits");
    Check(Hash(Path.Combine(a.InstallPath, "OptiScaler", Bc250RouteService.DllName)) == second.Hash, "restore refusal changes no DLL");
    service.Restore(b);
    Check(!File.Exists(bIni) && !File.Exists(Path.Combine(b.InstallPath, "winmm.dll")), "fresh install restores after repeat update");
    Check(File.ReadAllText(Path.Combine(b.InstallPath, "nvngx_dlss.dll")) == "original game helper", "restore preserves game helper");
    var c = GameAt("existing"); Make("existing/game.exe", "game");
    Make("existing/winmm.dll", File.ReadAllText(proxy));
    var originalIni = "[Libraries]\nOptiDllPath=auto\n[User]\nCustom=old\n; keep this comment\n";
    Make("existing/OptiScaler.ini", originalIni);
    var originalDll = Make("existing/OptiScaler/" + Bc250RouteService.DllName, "old FSR DLL");
    Make("existing/OptiScaler/unrelated.dll", "another mod");
    service.Install(c); service.Install(c); service.Restore(c);
    Check(File.ReadAllText(Path.Combine(c.InstallPath, "OptiScaler.ini")) == originalIni && File.ReadAllText(originalDll) == "old FSR DLL", "adoption and repeat update restore exact original DLL and INI");
    Check(File.Exists(Path.Combine(c.InstallPath, "winmm.dll")) && File.Exists(Path.Combine(c.InstallPath, "OptiScaler/unrelated.dll")), "restore preserves pre-existing OptiScaler and unrelated files");
    var busy = new Bc250RouteService(payload, state, _ => throw new IOException("game running"));
    Reject(() => busy.Install(c), "busy game deferred before writes");
    Check(File.ReadAllText(originalDll) == "old FSR DLL", "busy deferral preserves DLL");
    File.Delete(originalDll); File.CreateSymbolicLink(originalDll, first.File);
    Reject(() => service.Install(c), "shared symlink target rejected");
    Check(Hash(first.File) == first.Hash, "shared DLL not mutated");
    Reject(() => service.Import(Zip("bad", "bad", true)), "corrupt ZIP rejected");
    Check(service.CurrentRelease()!.Hash == second.Hash, "failed import preserves selected release");
    Reject(() => SafePath(root, "../outside"), "path escape rejected");
    var tx = Path.Combine(root, "tx"); var txState = Path.Combine(root, "tx-state");
    Make("tx/one", "old one"); Make("tx/two", "old two"); var replacement = Make("replacement", "new");
    Make("tx-state/receipt.json", "old receipt");
    try { Apply(tx, txState, new() { ["one"] = replacement, ["two"] = replacement }, "new receipt", _ => throw new Exception("injected write failure")); }
    catch (Exception ex) when (ex.Message == "injected write failure") { }
    Check(File.ReadAllText(Path.Combine(tx, "one")) == "old one" && File.ReadAllText(Path.Combine(txState, "receipt.json")) == "old receipt", "failed write rolls back files and receipt");
    var process = new ProcessStartInfo(Environment.ProcessPath!) { UseShellExecute = false };
    foreach (var arg in new[] { "--crash", tx, txState, replacement }) process.ArgumentList.Add(arg);
    using var child = Process.Start(process)!; child.WaitForExit();
    Check(child.ExitCode == 77 && File.Exists(Path.Combine(txState, "pending.json")), "interrupted process leaves durable recovery record");
    Recover(tx, txState);
    Check(File.ReadAllText(Path.Combine(tx, "one")) == "old one" && File.ReadAllText(Path.Combine(tx, "two")) == "old two" &&
        File.ReadAllText(Path.Combine(txState, "receipt.json")) == "old receipt", "interrupted process recovers previous files and receipt");
    Console.WriteLine($"All {passed} checks passed.");
}
finally { Directory.Delete(root, true); }
