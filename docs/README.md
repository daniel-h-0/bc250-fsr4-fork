# Documentation

**[Install across your games](optiscaler-client.md)** — use the BC250 build of
OptiScaler Client to install and update selected games together.
[Manual install and game recipes](beginner-guide.md) cover direct replacement,
native FidelityFX games and custom layouts.

## Find an answer

| Question | Read |
| --- | --- |
| How do I update or undo a client installation? | [Client update and restore](optiscaler-client.md#update-or-restore) |
| How do I check it is working? | [Verification and troubleshooting](beginner-guide.md#if-the-check-fails) |
| Why does first launch stall? | [Shader compilation](first-run-shader-compilation.md) |
| How much faster is it? | [GPU-cost chart and method](gpu-cost.md) |
| Does the custom driver improve on the DLL? | [DLL versus driver](driver-rc10.md#dll-versus-driver) |
| What was tested? | [Client validation](../integrations/optiscaler-client/README.md#validation), [RC11 DLL validation](portable-dll-rc11.md) |
| Which file should I download? | [Downloads](releases.md) |
| What changed? | [Client addon](releases.md#client-addon), [RC11 release notes](release-notes-rc11.md), [changelog](../CHANGELOG.md) |

## Optional routes

- [Shared shader cache](shared-shader-cache.md): reuse compatible compilations
  across selected Linux games; ordinary DLL installation needs no helper.
- [Private driver](driver-cache-setup.md): alternative AMD-provider integration,
  with its own [requirements](driver-rc10.md).
- [Old RC6 tool migration/recovery](legacy/runtime/legacy-rc6.md): for existing users of that tool.

## Development

[Contribute and run checks](../CONTRIBUTING.md) ·
[Rebuild the DLL](../dll/README.md) · [GPU probe](../dll/probe/README.md) ·
[Package a release](releases.md#packaging) ·
[Driver/runtime contracts](development.md) · [Licenses](../THIRD_PARTY.md)

[Client integration and build checks](../integrations/optiscaler-client/README.md)
cover the project-provided OptiScaler Client build.

## Historical documentation

[Browse the archive](legacy/README.md) for older releases and dated test records:

- **`legacy/runtime/`** — old Steam compatibility tool, system-driver setup,
  migration and recovery.
- **`legacy/research/`** — earlier DLL checkpoints, performance campaigns and
  cache/installer reviews.

The current installation guide stays above; code-specific build instructions
remain beside their code. Raw measurements and figures stay in `data/` and `assets/`.
