"""Couverture minimale pour la limite TRANSCRIBE_JOB_MAX_CONCURRENT (intra-process uvicorn)."""

from __future__ import annotations

import asyncio

import pytest

from routes import transcribe_jobs as tj


@pytest.fixture(autouse=True)
def _reset_slots_after_test() -> None:
    yield
    tj.reset_transcribe_job_slots_for_tests()


@pytest.mark.asyncio
async def test_unlimited_when_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRANSCRIBE_JOB_MAX_CONCURRENT", "0")
    tj.init_transcribe_job_slots()
    concurrent = 0
    peak = 0

    async def worker() -> None:
        nonlocal concurrent, peak
        async with tj.transcription_job_execution_slot():
            concurrent += 1
            peak = max(peak, concurrent)
            await asyncio.sleep(0.002)
            concurrent -= 1

    await asyncio.gather(*(worker() for _ in range(12)))
    assert concurrent == 0
    assert peak == 12
    assert tj.get_transcription_job_slot_capacity() is None


@pytest.mark.asyncio
async def test_respects_parallel_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRANSCRIBE_JOB_MAX_CONCURRENT", "2")
    tj.init_transcribe_job_slots()
    concurrent = 0
    peak = 0
    lock = asyncio.Lock()

    async def worker() -> None:
        nonlocal concurrent, peak
        async with tj.transcription_job_execution_slot():
            async with lock:
                concurrent += 1
                peak = max(peak, concurrent)
            await asyncio.sleep(0.02)
            async with lock:
                concurrent -= 1

    await asyncio.gather(*(worker() for _ in range(10)))
    assert concurrent == 0
    assert peak == 2
    assert tj.get_transcription_job_slot_capacity() == 2
