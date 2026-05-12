from transcription_retail_catalog import (
    LOYALTY_TIER_BOUNDARY_HOURS_1,
    LOYALTY_TIER_BOUNDARY_HOURS_2,
    canonical_transcription_model_id,
    get_retail_model,
    loyalty_tier_from_lifetime_hours,
    retail_mru_for_audio,
)


def test_canonical_openai_alias():
    assert canonical_transcription_model_id("openai") == "whisper-1"


def test_loyalty_tiers_boundaries():
    assert loyalty_tier_from_lifetime_hours(0) == "nouveau"
    assert loyalty_tier_from_lifetime_hours(LOYALTY_TIER_BOUNDARY_HOURS_1) == "nouveau"
    assert loyalty_tier_from_lifetime_hours(LOYALTY_TIER_BOUNDARY_HOURS_1 + 1e-6) == "regular"
    assert loyalty_tier_from_lifetime_hours(LOYALTY_TIER_BOUNDARY_HOURS_2 - 1e-6) == "regular"
    assert loyalty_tier_from_lifetime_hours(LOYALTY_TIER_BOUNDARY_HOURS_2) == "loyal"


def test_retail_mru_local_turbo_tier():
    spec = get_retail_model("local")
    mru, tier, bh, rate = retail_mru_for_audio(
        spec=spec,
        lifetime_hours_before_job=0,
        duration_seconds=3600,
    )
    assert tier == "nouveau"
    assert abs(rate - 2.0) < 1e-9
    assert abs(mru - 2.0) < 1e-9
    assert abs(bh - 1.0) < 1e-9


def test_retail_mru_whisper1_fidele():
    spec = get_retail_model("whisper-1")
    mru, tier, _, rate = retail_mru_for_audio(
        spec=spec,
        lifetime_hours_before_job=5.0,
        duration_seconds=3600,
    )
    assert tier == "loyal"
    assert abs(rate - 23.0) < 1e-9
    assert abs(mru - 23.0) < 1e-9
