// SPDX-License-Identifier: GPL-3.0-or-later
using System.Diagnostics;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
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
    svc.Import(args[2]);
    var results = new List<object>();
    foreach (var layout in new[] { "flat/Example.exe", "nested/bin/x64/Example.exe", "unreal/Project/Binaries/Win64/Example-Win64-Shipping.exe" })
    {
        var dir = Path.Combine(realRoot, layout.Split('/')[0]);
        var exe = Path.Combine(realRoot, layout); Directory.CreateDirectory(Path.GetDirectoryName(exe)!);
        File.Copy(args[4], exe, true);
        var game = new Game { Name = Path.GetFileNameWithoutExtension(exe), InstallPath = dir, ExecutablePath = exe, Platform = GamePlatform.Manual };
        var result = svc.Install(game);
        var receipt = svc.ReadReceipt(game)!;
        var ini = Path.Combine(receipt.Root, "OptiScaler.ini"); var firstIni = Hash(ini);
        svc.Install(game);
        if (Hash(ini) != firstIni) throw new Exception("Update changed INI");
        results.Add(new { game.Name, game.InstallPath, game.ExecutablePath, Proxy = "dxgi.dll", receipt.Root,
            Target = Path.Combine(receipt.Root, receipt.Target), Sha256 = Hash(Path.Combine(receipt.Root, receipt.Target)),
            Ini = ini, IniPreservedOnUpdate = true, Result = result });
    }
    File.WriteAllText(Path.Combine(realRoot, "results.json"), JsonSerializer.Serialize(results, Json));
    Console.WriteLine("PASS real payload installation and repeat updates for three general installation layouts");
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
string Exe(string relative, ushort machine = 0x8664)
{
    var path = Make(relative, "");
    using var file = new BinaryWriter(File.OpenWrite(path));
    file.BaseStream.SetLength(512);
    file.Write((ushort)0x5a4d); file.BaseStream.Position = 0x3c; file.Write(0x80);
    file.BaseStream.Position = 0x80; file.Write(0x4550); file.Write(machine);
    file.BaseStream.Position = 0x94; file.Write((ushort)240); file.Write((ushort)0x22);
    file.Write((ushort)0x20b);
    return path;
}
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
    Make("payload/OptiScaler.ini", "[Libraries]\nOptiDllPath=auto\nFfxDx12SRPath=auto\n[FSR]\nFsr4ForceModel=auto\n[Spoofing]\nDxgi=auto\n[Inputs]\nEnableDlssInputs=auto\n");
    Make("payload/OptiScaler/" + Bc250RouteService.DllName, "MZoriginal bundled DLL");
    Make("payload/OptiScaler/plugins/OptiPatcher.asi", "MZplugin");
    Make("payload/nvngx_dlss.dll", "MZhelper");
    var manifest = new Bc250RouteService.Payload(Hash(proxy),
        Directory.GetFiles(payload, "*", SearchOption.AllDirectories).ToDictionary(f => Path.GetRelativePath(payload, f), Hash));
    Make("payload/payload.json", JsonSerializer.Serialize(manifest));
    var state = Path.Combine(root, "state");
    var service = new Bc250RouteService(payload, state, _ => { });
    var first = service.Import(Zip("test1", "first"));
    var a = GameAt("a"); var b = GameAt("b");
    foreach (var game in new[] { a, b })
    {
        Exe(game.Name + "/game.exe");
        Make(game.Name + "/nvngx_dlss.dll", "original game helper");
        service.Install(game);
        Check(Hash(Path.Combine(game.InstallPath, "OptiScaler", Bc250RouteService.DllName)) == first.Hash, "correct nested DLL: " + game.Name);
        Check(File.ReadAllText(Path.Combine(game.InstallPath, "nvngx_dlss.dll")) == "original game helper", "existing helper preserved: " + game.Name);
        Check(Bc250RouteService.Get(File.ReadAllText(Path.Combine(game.InstallPath, "OptiScaler.ini")), "FSR", "Fsr4ForceModel") == "2", "INT8 preset: " + game.Name);
    }
    var aIni = Path.Combine(a.InstallPath, "OptiScaler.ini"); var bIni = Path.Combine(b.InstallPath, "OptiScaler.ini");
    Check(File.ReadAllText(aIni) == File.ReadAllText(bIni) &&
        Bc250RouteService.Get(File.ReadAllText(aIni), "Spoofing", "Dxgi") == "auto" &&
        Bc250RouteService.Get(File.ReadAllText(aIni), "Inputs", "EnableDlssInputs") == "auto", "same general setup preserves upstream input/spoofing defaults");
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
    File.WriteAllText(Path.Combine(b.InstallPath, "dxgi.dll"), "different adapter");
    Reject(() => service.Install(b), "changed adapter held for review");
    File.Copy(proxy, Path.Combine(b.InstallPath, "dxgi.dll"), true);
    Reject(() => service.Restore(a), "restore refuses later user edits");
    Check(Hash(Path.Combine(a.InstallPath, "OptiScaler", Bc250RouteService.DllName)) == second.Hash, "restore refusal changes no DLL");
    service.Restore(b);
    Check(!File.Exists(bIni) && !File.Exists(Path.Combine(b.InstallPath, "dxgi.dll")), "fresh install restores after repeat update");
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
    var general = GameAt("unlisted");
    general.ExecutablePath = Exe("unlisted/ActualGame.exe");
    var generalPlan = service.Describe(general);
    Check(!generalPlan.Existing && generalPlan.Hint.Contains(general.InstallPath), "unlisted x64 game gets general setup with visible folder");
    service.Install(general);
    var generalIni = Path.Combine(general.InstallPath, "OptiScaler.ini");
    Check(Hash(Path.Combine(general.InstallPath, "OptiScaler", Bc250RouteService.DllName)) == second.Hash &&
        Hash(Path.Combine(general.InstallPath, "dxgi.dll")) == Hash(proxy), "general setup places adapter and BC250 DLL");
    Check(Bc250RouteService.Get(File.ReadAllText(generalIni), "Plugins", "LoadAsiPlugins") == "true" &&
        Bc250RouteService.Get(File.ReadAllText(generalIni), "FSR", "Fsr4ForceModel") == "2", "general setup enables patcher and INT8");
    var generalBefore = File.ReadAllBytes(generalIni); service.Install(general);
    Check(File.ReadAllBytes(generalIni).SequenceEqual(generalBefore), "general update preserves settings");
    service.Restore(general);
    Check(Directory.GetFiles(general.InstallPath).SequenceEqual(new[] { general.ExecutablePath }), "general restore retains only original executable");
    Make("unlisted/dxgi.dll", "another mod");
    Reject(() => service.Install(general), "general setup preserves proxy-name conflict");
    Check(!File.Exists(generalIni) && File.ReadAllText(Path.Combine(general.InstallPath, "dxgi.dll")) == "another mod", "proxy conflict causes no writes");
    var native = GameAt("native"); native.ExecutablePath = Make("native/linux-game", "ELF");
    Reject(() => service.Install(native), "native executable rejected");
    var x86 = GameAt("x86"); x86.ExecutablePath = Exe("x86/title.exe", 0x14c);
    Reject(() => service.Install(x86), "32-bit executable rejected");
    var detected = GameAt("unreal"); detected.Platform = GamePlatform.Steam;
    Exe("unreal/Launcher.exe"); Exe("unreal/Project/Binaries/Win64/Title-Win64-Shipping.exe");
    Check(service.Describe(detected).Root.EndsWith("Project/Binaries/Win64"), "general scan finds Unreal shipping directory");
    Exe("unreal/Project/Binaries/Win64/Second.exe");
    Reject(() => service.Describe(detected), "ambiguous executable selection requests manual choice");
    detected.Platform = GamePlatform.Manual;
    detected.ExecutablePath = Path.Combine(detected.InstallPath, "Project/Binaries/Win64/Second.exe");
    Check(service.Describe(detected).Hint.Contains("Second.exe"), "explicit executable resolves ambiguity");
    service.Install(detected);
    var duplicate = new Game { Name = "duplicate", InstallPath = Path.GetDirectoryName(detected.ExecutablePath)!, ExecutablePath = detected.ExecutablePath, Platform = GamePlatform.Manual };
    Reject(() => service.Install(duplicate), "duplicate library entry cannot take ownership of managed files");
    service.Restore(detected);
    var linked = GameAt("linked"); linked.ExecutablePath = Path.Combine(linked.InstallPath, "Linked.exe");
    Directory.CreateDirectory(linked.InstallPath); File.CreateSymbolicLink(linked.ExecutablePath, general.ExecutablePath);
    Reject(() => service.Describe(linked), "general executable symlink rejected");
    foreach (var name in new[] { "d3d12.dll", "wininet.dll" })
    {
        var extra = GameAt("adapter-" + name); Make(extra.Name + "/game.exe", "game");
        Make(extra.Name + "/" + name, File.ReadAllText(proxy));
        Make(extra.Name + "/OptiScaler.ini", originalIni);
        Make(extra.Name + "/OptiScaler/" + Bc250RouteService.DllName, "existing DLL");
        service.Install(extra); service.Restore(extra);
        Check(File.ReadAllText(Path.Combine(extra.InstallPath, "OptiScaler.ini")) == originalIni, "adopt and restore supported proxy " + name);
    }
    var legacy = GameAt("previous-client"); Make(legacy.Name + "/game.exe", "original executable");
    var legacyFiles = new Dictionary<string, string> {
        ["winmm.dll"] = File.ReadAllText(proxy),
        ["OptiScaler.ini"] = "[Libraries]\nOptiDllPath=auto\n[Spoofing]\nDxgi=false\n[User]\nCustom=preserve\n",
        ["OptiScaler/" + Bc250RouteService.DllName] = "previous DLL"
    };
    var owned = new List<Bc250RouteService.OwnedFile>();
    foreach (var (name, content) in legacyFiles)
    {
        var file = Make(legacy.Name + "/" + name, content);
        owned.Add(new(name, null, Hash(file), "unused"));
    }
    var legacyKey = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(legacy.InstallPath))).ToLowerInvariant();
    var legacyState = Path.Combine(state, "games", legacyKey); Directory.CreateDirectory(legacyState);
    File.WriteAllText(Path.Combine(legacyState, "receipt.json"), JsonSerializer.Serialize(
        new Bc250RouteService.Receipt(1, legacy.InstallPath, "OptiScaler/" + Bc250RouteService.DllName, "4.0.0-rc11-docs1", owned, "Earlier loading instructions")));
    service.Install(legacy);
    Check(File.ReadAllText(Path.Combine(legacy.InstallPath, "OptiScaler.ini")) == legacyFiles["OptiScaler.ini"] &&
        !File.Exists(Path.Combine(legacy.InstallPath, "dxgi.dll")), "previous client receipt keeps its adapter and all settings on update");
    service.Restore(legacy);
    Check(Directory.GetFiles(legacy.InstallPath).SequenceEqual(new[] { legacy.ExecutablePath }), "previous client receipt restores original tree");
    Reject(() => service.Import(Zip("bad", "bad", true)), "corrupt ZIP rejected");
    Check(service.CurrentRelease()!.Hash == second.Hash, "failed import preserves selected release");
    Reject(() => SafePath(root, "../outside"), "path escape rejected");
    // Fedora Atomic/Bazzite: $HOME is /home/<user> and /home links to var/home.
    var linkedHome = Path.Combine(root, "home"); Directory.CreateDirectory(Path.Combine(root, "var-home"));
    Directory.CreateSymbolicLink(linkedHome, Path.Combine(root, "var-home"));
    Exe("var-home/atomic/Game.exe");
    var atomic = new Game { Name = "atomic", InstallPath = Path.Combine(linkedHome, "atomic"), ExecutablePath = Path.Combine(linkedHome, "atomic", "Game.exe"), Platform = GamePlatform.Manual };
    service.Install(atomic);
    Check(Hash(Path.Combine(atomic.InstallPath, "OptiScaler", Bc250RouteService.DllName)) == second.Hash, "game below a linked home folder installs");
    service.Restore(atomic);
    Check(Directory.GetFiles(atomic.InstallPath).SequenceEqual(new[] { atomic.ExecutablePath }), "game below a linked home folder restores");
    // The same game may be listed as /home/<user>/… and /var/home/<user>/….
    var atomicReal = new Game { Name = "atomic-real", InstallPath = Path.Combine(root, "var-home", "atomic"), ExecutablePath = Path.Combine(root, "var-home", "atomic", "Game.exe"), Platform = GamePlatform.Manual };
    bool AtomicRestored() => Directory.GetFiles(atomic.InstallPath).SequenceEqual(new[] { atomic.ExecutablePath }) && !service.HasRecord(atomic) && !service.HasRecord(atomicReal);
    service.Install(atomic);
    Check(service.ReadReceipt(atomicReal) != null && service.Describe(atomicReal).Existing, "other spelling finds the same installation");
    service.Install(atomicReal); service.Restore(atomicReal);
    Check(AtomicRestored(), "either spelling updates and restores one record");
    var mixed = new Game { Name = "mixed", InstallPath = atomic.InstallPath, ExecutablePath = atomicReal.ExecutablePath, Platform = GamePlatform.Manual };
    Check(service.Describe(mixed).Root == atomic.InstallPath, "executable chosen through the other spelling stays inside its entry");
    string KeyOf(string path) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(path))).ToLowerInvariant();
    var gameRecords = Path.Combine(state, "games");
    service.Install(atomic);
    Directory.Move(Path.Combine(gameRecords, KeyOf(RealPath(atomic.InstallPath))), Path.Combine(gameRecords, KeyOf(atomic.InstallPath)));
    Check(service.ReadReceipt(atomic) != null, "record keyed by the written folder (bc250.4) still found");
    Reject(() => service.Install(atomicReal), "unmoved earlier record is not installed twice through the other spelling");
    service.Install(atomic);
    Check(Directory.Exists(Path.Combine(gameRecords, KeyOf(RealPath(atomic.InstallPath)))) &&
        !Directory.Exists(Path.Combine(gameRecords, KeyOf(atomic.InstallPath))), "next update moves the earlier record to the resolved folder");
    var guarded = new Bc250RouteService(payload, state);
    using (var running = Process.Start(new ProcessStartInfo("/bin/sh") { ArgumentList = { "-c", "sleep 30; :", RealPath(atomicReal.ExecutablePath) }, UseShellExecute = false })!)
    {
        try { guarded.Install(atomic); throw new Exception("Did not refuse: game running through the other spelling"); }
        catch (InvalidOperationException) { Check(true, "running game found through either spelling"); }
        finally { running.Kill(); running.WaitForExit(); }
    }
    service.Restore(atomicReal);
    Check(AtomicRestored(), "moved record restores through the other spelling");
    Directory.CreateSymbolicLink(Path.Combine(root, "var-home/atomic/Linked"), b.InstallPath);
    Reject(() => SafePath(atomic.InstallPath, "Linked/file"), "linked directory inside game folder rejected");
    Reject(() => SafePath(linkedHome, "file"), "linked game folder itself rejected");
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
