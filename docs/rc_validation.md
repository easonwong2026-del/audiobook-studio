# 3.3.3 RC validation

This document records reproducible local diagnostics for the Phase 4
consolidation. It is not a Release/Tag checklist and does not replace the final
Windows + real IndexTTS2 acceptance run.

## Commands

```bash
python scripts/benchmark_app_runtime.py
python scripts/benchmark_scaling.py --sizes 1000 5000 10000 --status-updates 1000
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
