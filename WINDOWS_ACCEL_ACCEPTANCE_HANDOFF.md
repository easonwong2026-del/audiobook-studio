# Windows GPT Accel Acceptance Handoff

Branch: `perf/index-tts25-gpt-accel-production`

Commit: branch tip reported in the integration report

## Changed files

- `lib/tts_engine.py` — IndexTTS 2.5 Accel capability resolution, external overlay loading, process-local CC, scoped Triton cache workaround, active status.
- `tests/test_tts_accel_integration.py` — GPU-free capability, lifecycle boundary, overlay, CC, status, and workaround coverage.

## Mac validation

Targeted production/runtime tests pass on Mac. CUDA, FlashAttention kernels, Triton JIT, CUDA Graph, RTX 5070 Ti behavior, and audio quality remain unvalidated here.

`MAC_GPU_VALIDATION = NOT APPLICABLE`

## Runtime contract

- Use a read-only production venv with Python 3.11.13, Torch 2.8.0+cu128, and Torchaudio 2.8.0+cu128.
- Set `AUDIOBOOK_STUDIO_ACCEL_OVERLAY` only to the external overlay `site-packages` directory. The app prepends it for this process only; it does not install packages, set `setx`, edit the registry, or modify the venv.
- Optional emergency rollback: `AUDIOBOOK_STUDIO_DISABLE_INDEXTTS25_ACCEL=1`.
- If Triton needs the bundled compiler, the app resolves `triton/runtime/tcc/tcc.exe` from the imported package and sets process-local `CC`; a valid existing `CC` is preserved.
- Expected status/log fields: `requested`, `available`, `enabled`, `active`, `fallback`, `reason`, `flash_attn_version`, `triton_version`. The in-process path-free getter is `lib.tts_engine.get_acceleration_status()`.

## Windows checklist

- [ ] Production venv remains Torch 2.8.0+cu128.
- [ ] Production venv remains Torchaudio 2.8.0+cu128.
- [ ] External overlay contains validated FlashAttention 2.8.3.
- [ ] External overlay contains triton-windows 3.4.0.post21.
- [ ] Runtime reports actual `GPT2AccelModel` / `AccelInferenceEngine` active.
- [ ] Runtime reports `fallback=false`.
- [ ] CUDA Graph captured `[1,2,4,8]`.
- [ ] Graph replay count is greater than zero and eager count is zero.
- [ ] KV cache lifecycle is balanced.
- [ ] 50+ real segments succeed with no OOM or crash.
- [ ] Multi-role and multi-emotion synthesis succeeds.
- [ ] Repair / selected-segment resynthesis reuses the warm engine without a new initialization or graph capture.
- [ ] #87 CUDA cache cleanup policy is unchanged.
- [ ] P versus PA speed gain is reproduced.
- [ ] Manual audio A/B is accepted using matched baseline and Accel-only pairs.

Checkout this branch on the Windows WorkBuddy. Do not merge it into `main`, modify the production venv, or mark the PR ready until this checklist passes on the RTX 5070 Ti with real Studio data.
