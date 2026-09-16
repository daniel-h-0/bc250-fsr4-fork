# Documentation

**[Install or update the DLL](beginner-guide.md)** — replace OptiScaler's bundled
upscaler DLL, select FFX/INT8 and keep working launch settings. First-time adapter
setup and game recipes are included there.

## Find an answer

| Question | Read |
| --- | --- |
| How do I check it is working, or undo it? | [Installation and troubleshooting](beginner-guide.md#if-the-check-fails) |
| Why does first launch stall? | [Shader compilation](first-run-shader-compilation.md) |
| How much faster is it? | [GPU-cost chart and method](gpu-cost.md) |
| Does the custom driver improve on the DLL? | [DLL versus driver](driver-rc10.md#dll-versus-driver) |
| What did this release test? | [RC11 validation](portable-dll-rc11.md) |
| Which file should I download? | [Downloads](releases.md) |
| What changed? | [RC11 release notes](release-notes-rc11.md), [changelog](../CHANGELOG.md) |

## Optional routes

- [Shared shader cache](shared-shader-cache.md): reuse compatible compilations
  across selected Linux games; ordinary DLL installation needs no helper.
- [Private driver](driver-cache-setup.md): alternative AMD-provider integration,
  with its own [requirements](driver-rc10.md).
- [Old RC6 tool migration/recovery](legacy-rc6.md): for existing users of that tool.

## Development

[Contribute and run checks](../CONTRIBUTING.md) ·
[Rebuild the DLL](../dll/README.md) · [GPU probe](../dll/probe/README.md) ·
[Package a release](releases.md#packaging) ·
[Driver/runtime contracts](development.md) · [Licenses](../THIRD_PARTY.md)

<details>
<summary>Historical evidence and recovery records</summary>

These retain their original versions and test scope. They are not additional
steps for the current install.

| Topic | Records |
| --- | --- |
| DLL development | [RC7 game checks](portable-dll-rc7.md), [RC8](portable-dll-rc8.md), [RC9](portable-dll-rc9.md), [RC10](portable-dll-rc10.md) |
| Performance campaigns | [RC7 GPU costs](gpu-cost-rc7.md), [whole-game comparison](performance.md), [reconstructed pass costs](fsr-cost.md) |
| Driver and cache work | [RC10 development](rc10-development.md), [driver gameplay](driver-gameplay-rc10.md), [cache reuse](cache-setup-qualification.md), [installer review](cache-review.md), [recovery review](cache-review2.md) |
| Earlier installs | [RC9 walkthrough](beginner-guide-rc9.md), [RC6 Steam tool](games.md), [legacy troubleshooting](game-troubleshooting.md), [upstream archive](../legacy/README.md) |

</details>
