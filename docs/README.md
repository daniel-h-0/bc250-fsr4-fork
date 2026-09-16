# Documentation

**Start with [Install with OptiScaler](beginner-guide.md): replace one DLL,
select FFX/INT8, keep your working launch settings.** It contains the downloads,
per-game settings, native alternatives, verification, troubleshooting and undo.

## Using the DLL

| Need | Page |
| --- | --- |
| Install or update | [Installation guide](beginner-guide.md) |
| First launch stalls | [Shader compilation](first-run-shader-compilation.md) |
| See performance results | [FSR4 GPU-cost chart and data](gpu-cost.md) |
| Compare DLL and driver routes | [What differs](driver-rc10.md#dll-versus-driver) |
| Check what RC11 actually tested | [Validation](portable-dll-rc11.md) |
| Find downloads or release changes | [Releases](releases.md), [RC11 notes](release-notes-rc11.md) |

## Optional tools

The default installation replaces one DLL in each game's existing OptiScaler
folder. These tools are optional additions, not follow-up steps.

- [Shared shader cache](shared-shader-cache.md): optional reuse across games.
- [Private Linux driver](driver-cache-setup.md): alternative for existing
  AMD-provider integrations; not an extra optimization to install over the DLL.
- [Old RC6 tool migration and recovery](legacy-rc6.md): only for existing users
  of that compatibility tool.

## Building and reviewing

[Contributing](../CONTRIBUTING.md) · [DLL rebuild](../dll/README.md) ·
[GPU probe](../dll/probe/README.md) · [Release packaging](releases.md#packaging) ·
[Licenses](../THIRD_PARTY.md)

## Historical evidence

The remaining versioned reports preserve measurements, old workflows and
recovery details. They are not additional steps for a current installation.

- [RC7 game checks](portable-dll-rc7.md), [RC8](portable-dll-rc8.md),
  [RC9 shader checkpoint](portable-dll-rc9.md), [RC10 compilation work](portable-dll-rc10.md).
- [RC10 development record](rc10-development.md),
  [driver gameplay](driver-gameplay-rc10.md), [cache qualification](cache-setup-qualification.md),
  [first](cache-review.md) and [second](cache-review2.md) installer reviews.
- [Earlier GPU-cost chart](gpu-cost-rc7.md), [whole-game comparison](performance.md),
  [reconstructed pass costs](fsr-cost.md). These are different experiments.
- [RC6 Steam tool](games.md), [legacy troubleshooting](game-troubleshooting.md),
  [RC9 beginner guide](beginner-guide-rc9.md), [older release history](../CHANGELOG.md).
