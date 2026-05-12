"""Régression : gpt-4o*transcribe n'accepte pas verbose_json côté API OpenAI."""

from routes import transcribe as tr


def test_gpt4o_mini_transcribe_no_verbose_json():
    assert tr._openai_transcription_supports_verbose_json("gpt-4o-mini-transcribe") is False


def test_whisper_one_verbose_ok():
    assert tr._openai_transcription_supports_verbose_json("whisper-1") is True


def test_gpt4o_transcribe_diarize_no_verbose_json():
    assert tr._openai_transcription_supports_verbose_json("gpt-4o-transcribe-diarize") is False
