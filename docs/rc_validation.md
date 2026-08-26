# 3.3.3 RC validation

This document records reproducible local diagnostics for the Phase 4
consolidation. It is not a Release/Tag checklist and does not replace the final
Windows + real configured IndexTTS 2 / 2.5 acceptance run.

## Commands

```bash
python scripts/benchmark_app_runtime.py
python scripts/benchmark_scaling.py --sizes 1000 5000 10000 --status-updates 1000
python scripts/benchmark_formal_export.py --segments 1000
python scripts/benchmark_vram_policy.py --speaker /path/to/real-speaker.wav
python -m pytest tests/ -q
```

The VRAM command requires the configured real model, CUDA and a real speaker
sample. CPU or fake-engine output must not be reported as VRAM/TTS acceptance.

## Local macOS control-plane result

Environment: Python 3.12, Gradio 5.50, no Torch/CUDA/model/FFmpeg in the test
venv.

| Metric | 1k segments | 5k segments | 10k segments |
| --- | ---: | ---: | ---: |
| Project open | 0.0047 s | 0.0202 s | 0.0398 s |
| QA refresh | 0.0382 s | 0.1866 s | 0.3739 s |
| Task query | 0.0022 s | 0.0040 s | 0.0060 s |
| 1,000 status updates | 0.0827 s | 0.1036 s | 0.1326 s |
| Streaming WAV core | 0.0435 s | 0.2175 s | 0.4605 s |
| Streaming peak Python allocation | 0.035 MB | 0.038 MB | 0.023 MB |

The expanded QA benchmark uses real project-local tiny WAV files and separates
analysis from persistence.  On the same macOS control-plane environment:

| Metric | 1k | 5k | 10k |
| --- | ---: | ---: | ---: |
| QA analyze wall time | 0.516 s | 2.611 s | 5.095 s |
| QA batch persistence wall time | 0.176 s | 0.923 s | 1.855 s |
| Post-QA report refresh | 0.515 s | 2.586 s | 5.351 s |

Batch persistence performs one cross-process lock and one atomic
`quality_state.json` replacement per batch, rather than one replacement per
segment.  The Python allocation figures are diagnostic; they are not a
substitute for process RSS.

Formal export has a separate reproducible benchmark:
`scripts/benchmark_formal_export.py`.  It uses WAV output and the runtime's
durable async job, records wall time, peak RSS where the platform exposes it,
Python allocation peak, and published artifact bytes.  It does not claim real
IndexTTS 2 / 2.5 inference, CUDA, FFmpeg M4B, or listening acceptance.

One local 1k-segment run completed with status `done` in 5.070 s, 151.6 MB
process peak RSS, 24.8 MB peak Python allocation, and a 17.6 MB published WAV
artifact.  RSS includes project setup and quality-state construction; repeat
the benchmark on the target Windows machine before treating it as a delivery
capacity limit.

App cold process wall time was 1.205 s; app import was 1.110 s; UI idle RSS was
147.1 MB. These are local development figures, not Windows performance
guarantees.

The status benchmark uses one long-lived line-buffered recovery journal,
chapter-boundary `fsync`, and one task-boundary `project.json` consolidation.
Before keeping the journal handle open, the same 10k-project/1,000-update
benchmark took about 3.83 s; it now takes about 0.13 s.

## Visual smoke

The local Gradio app was checked at:

- 1280×720 with device scale factor 1.25
- 1440×900 with device scale factor 1.50
- 1920×1080 with device scale factor 1.00

All five main pages plus Settings had no page-level horizontal overflow. This
Chromium emulation does not replace a real Windows 125%/150% manual check.
