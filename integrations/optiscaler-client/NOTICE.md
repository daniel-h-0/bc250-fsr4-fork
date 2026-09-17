# BC250 FSR4 client integration

This is a project build of [OptiScaler Client](https://github.com/Optiscaler-Client/Optiscaler-Client)
1.0.7, upstream commit `dd534b7d1cb8a0edf174a6917f5179791603d364`.

OptiScaler Client is Copyright (C) 2026 Agustín Montaña (Agustinm28) and
contributors, licensed GPL-3.0-or-later. The BC250 C# additions carry the same
license, Copyright (c) 2026 BC250 FSR4 contributors. The full license and complete
modified client source accompany the binary. The Python preparation, packaging and cache/launcher
tools are separately SPDX-marked MIT under this project's LICENSE.new-code.

Upstream supplies the desktop application, library discovery and component
management services. BC250 adds a dedicated FSR4 installation/update/restore
workflow, optional shared-cache enrollment, FFX/INT8 defaults, dependency pins and distribution packaging.
[OptiScaler](https://github.com/optiscaler/OptiScaler) and
[OptiPatcher](https://github.com/optiscaler/OptiPatcher) are separate software by
the OptiScaler team and contributors: the in-game upscaler adapter and its
input-compatibility plugin. OptiScaler Client is an independent manager project.

The `source.tar.gz` file contains the complete modified client, integration
sources, generated upstream patch and packaging recipe. Build the client with
the .NET 10 SDK using `dotnet publish -c Release -r linux-x64 --self-contained true`.
The client csproj identifies dependency versions. Reproduce the full package from
the matching BC250 FSR4 source checkout using scripts/package-opticlient.py.

This distribution includes the project's separately licensed FSR4 DLL ZIP,
including AMD notices and provenance, unchanged. It downloads OptiScaler,
OptiPatcher and the NVIDIA DLSS helper from their pinned upstream URLs during
first setup; those binaries are not redistributed in the client archive.
Their upstream terms and downloaded notices apply. The graphics driver and Proton
are supplied by the user.

This build is maintained by BC250 FSR4, not an official OptiScaler Client release.
Its game records are kept separately under OptiscalerClient-BC250. Obtain updates
to this application from BC250 FSR4; the upstream application's update check is
disabled in this build so it cannot replace the integration silently.
