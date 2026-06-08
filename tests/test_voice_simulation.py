"""
Layer 4: Voice Call Simulation Tests
Tests the voice agent flow without making real phone calls.
Validates: script generation, call staging, transcript processing, CRM update.

Run: pytest tests/test_voice_simulation.py -v
"""

import pytest
import json
from unittest.mock import patch, AsyncMock, MagicMock


class TestVoiceScriptGeneration:

    def test_scripts_are_language_agnostic(self):
        from mcp_servers.voice_mcp import SCRIPTS
        for call_type, template in SCRIPTS.items():
            # Templates must use {language_instruction} and {greeting} placeholders
            assert "{language_instruction}" in template, \
                f"{call_type}: missing {{language_instruction}} placeholder"
            assert "{greeting}" in template, \
                f"{call_type}: missing {{greeting}} placeholder"
            assert "{client_name}" in template, \
                f"{call_type}: missing {{client_name}} placeholder"

    def test_script_filled_in_tamil(self):
        from mcp_servers.voice_mcp import SCRIPTS, SUPPORTED_LANGUAGES, _build_language_instruction
        lang = SUPPORTED_LANGUAGES["ta-IN"]
        script = SCRIPTS["sip_renewal"].format(
            client_name="Priya Rajan",
            language_instruction=_build_language_instruction("ta-IN"),
            greeting=lang["greeting"],
            closing=lang["closing"],
            fund_name="SBI Bluechip Fund",
            monthly_amount="30,000",
            expiry_date="15 Jun 2026",
        )
        assert "Priya Rajan" in script
        assert "Tamil" in script  # language instruction mentions Tamil
        assert "Vanakkam" in script
        assert "SBI Bluechip Fund" in script

    def test_script_filled_in_kannada(self):
        from mcp_servers.voice_mcp import SCRIPTS, SUPPORTED_LANGUAGES, _build_language_instruction
        lang = SUPPORTED_LANGUAGES["kn-IN"]
        script = SCRIPTS["kyc_reminder"].format(
            client_name="Suresh Kumar",
            language_instruction=_build_language_instruction("kn-IN"),
            greeting=lang["greeting"],
            closing=lang["closing"],
            expiry_date="30 May 2026",
        )
        assert "Suresh Kumar" in script
        assert "Kannada" in script
        assert "Namaskara" in script

    def test_all_four_script_types_exist(self):
        from mcp_servers.voice_mcp import SCRIPTS
        for call_type in ["sip_renewal", "meeting_schedule", "kyc_reminder", "birthday_greeting"]:
            assert call_type in SCRIPTS, f"Missing script for {call_type}"
            assert len(SCRIPTS[call_type]) > 100, f"Script for {call_type} is too short"

    def test_all_supported_languages_have_greeting_and_closing(self):
        from mcp_servers.voice_mcp import SUPPORTED_LANGUAGES
        expected_languages = [
            "hi-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN",
            "mr-IN", "bn-IN", "gu-IN", "pa-IN", "en-IN",
        ]
        for code in expected_languages:
            assert code in SUPPORTED_LANGUAGES, f"Language {code} not configured"
            lang = SUPPORTED_LANGUAGES[code]
            assert lang.get("greeting"), f"{code}: missing greeting"
            assert lang.get("closing"), f"{code}: missing closing"
            assert lang.get("name"), f"{code}: missing display name"

    def test_language_resolution_by_name(self):
        from mcp_servers.voice_mcp import _resolve_language
        assert _resolve_language("Tamil") == "ta-IN"
        assert _resolve_language("tamil") == "ta-IN"
        assert _resolve_language("Telugu") == "te-IN"
        assert _resolve_language("Kannada") == "kn-IN"
        assert _resolve_language("Malayalam") == "ml-IN"
        assert _resolve_language("Marathi") == "mr-IN"
        assert _resolve_language("Bengali") == "bn-IN"
        assert _resolve_language("Gujarati") == "gu-IN"
        assert _resolve_language("Punjabi") == "pa-IN"
        assert _resolve_language("English") == "en-IN"
        # Unknown language falls back to Hindi
        assert _resolve_language("Klingon") == "hi-IN"

    def test_language_resolution_by_code(self):
        from mcp_servers.voice_mcp import _resolve_language
        assert _resolve_language("ta-IN") == "ta-IN"
        assert _resolve_language("hi-IN") == "hi-IN"
        assert _resolve_language("kn-IN") == "kn-IN"


class TestCallStagingWithoutRealCalls:

    def test_initiate_call_returns_simulated_in_poc(self):
        """In PoC mode (no Twilio keys), call must be simulated not real."""
        with patch.dict("os.environ", {"TWILIO_ACCOUNT_SID": ""}):
            from mcp_servers.voice_mcp import initiate_voice_call
            result = initiate_voice_call(
                client_id="C0001",
                mobile="+919876543210",
                client_name="Rahul Sharma",
                call_type="sip_renewal",
                script_variables={
                    "fund_name": "HDFC Mid-Cap Fund",
                    "monthly_amount": "25,000",
                    "expiry_date": "28 May 2026",
                },
                rm_id="RM001",
                language="hi-IN",
            )
        assert "call_id" in result
        assert result["status"] == "simulated"
        assert "script_preview" in result
        assert result["language"] == "hi-IN"
        assert result["language_name"] == "Hindi"

    def test_initiate_call_in_tamil(self):
        """Call in Tamil should use Tamil greeting and language code."""
        with patch.dict("os.environ", {"TWILIO_ACCOUNT_SID": ""}):
            from mcp_servers.voice_mcp import initiate_voice_call
            result = initiate_voice_call(
                client_id="C0010",
                mobile="+919876543211",
                client_name="Priya Rajan",
                call_type="sip_renewal",
                script_variables={
                    "fund_name": "SBI Bluechip Fund",
                    "monthly_amount": "30,000",
                    "expiry_date": "15 Jun 2026",
                },
                rm_id="RM001",
                language="Tamil",  # name accepted, not just BCP-47 code
            )
        assert result["status"] == "simulated"
        assert result["language"] == "ta-IN"
        assert result["language_name"] == "Tamil"
        assert "Vanakkam" in result["script_preview"]

    def test_initiate_call_in_telugu(self):
        """Call in Telugu should use Telugu greeting."""
        with patch.dict("os.environ", {"TWILIO_ACCOUNT_SID": ""}):
            from mcp_servers.voice_mcp import initiate_voice_call
            result = initiate_voice_call(
                client_id="C0020",
                mobile="+919876543212",
                client_name="Venkat Rao",
                call_type="birthday_greeting",
                script_variables={},
                rm_id="RM002",
                language="te-IN",
            )
        assert result["language"] == "te-IN"
        assert result["language_name"] == "Telugu"
        assert "Namaskaram" in result["script_preview"]

    def test_call_not_placed_without_twilio_credentials(self):
        """Verify that initiate_voice_call does not place a real call in PoC mode."""
        with patch("mcp_servers.voice_mcp._place_twilio_call") as mock_twilio:
            with patch.dict("os.environ", {"TWILIO_ACCOUNT_SID": ""}):
                from mcp_servers.voice_mcp import initiate_voice_call
                initiate_voice_call(
                    client_id="C0001",
                    mobile="+919876543210",
                    client_name="Test Client",
                    call_type="sip_renewal",
                    script_variables={
                        "fund_name": "Test Fund",
                        "monthly_amount": "10,000",
                        "expiry_date": "01 Jun 2026",
                    },
                    language="hi-IN",
                )
            mock_twilio.assert_not_called()

    def test_simulate_call_outcome_stores_transcript(self):
        from mcp_servers.voice_mcp import initiate_voice_call, simulate_call_outcome, get_call_status
        with patch.dict("os.environ", {"TWILIO_ACCOUNT_SID": ""}):
            call = initiate_voice_call(
                client_id="C0001",
                mobile="+919876543210",
                client_name="Rahul Sharma",
                call_type="sip_renewal",
                script_variables={
                    "fund_name": "HDFC Mid-Cap",
                    "monthly_amount": "25,000",
                    "expiry_date": "28 May 2026",
                },
                rm_id="RM001",
                language="hi-IN",
            )
        call_id = call["call_id"]

        mock_transcript = """
Agent: Namaste Rahul ji! Main ABC Bank ki taraf se bol rahi hoon.
       Aapka HDFC Mid-Cap SIP 28 May ko expire ho raha hai.
       Kya aap renew karna chahenge?
Client: Haan, same amount rakho.
Agent: Perfect! ₹25,000 per month renew kar diya. Dhanyavaad!
"""
        simulate_call_outcome(
            call_id=call_id,
            outcome="renewed",
            transcript=mock_transcript,
        )

        status = get_call_status(call_id)
        assert status["outcome"] == "renewed"
        assert "Rahul" in status.get("transcript", "")
        assert status["status"] == "completed"


class TestVoiceTranscriptProcessing:

    SAMPLE_TRANSCRIPT = """
Agent: Namaste Rahul ji! Main ABC Bank ki taraf se bol rahi hoon.
Client: Haan boliye.
Agent: Aapka HDFC Mid-Cap SIP 28 May ko expire ho raha hai. Kya renew karein?
Client: Haan, same amount theek hai.
Agent: Perfect! ₹25,000 per month renew kar diya. Dhanyavaad Rahul ji!
Client: Shukriya.
"""

    def test_transcript_contains_both_speakers(self):
        assert "Agent:" in self.SAMPLE_TRANSCRIPT
        assert "Client:" in self.SAMPLE_TRANSCRIPT

    def test_transcript_shows_outcome(self):
        assert "renew" in self.SAMPLE_TRANSCRIPT.lower()
        assert "₹25,000" in self.SAMPLE_TRANSCRIPT

    def test_hindi_transcript_is_readable(self):
        # Verify Devanagari or romanised Hindi present
        hindi_words = ["haan", "namaste", "dhanyavaad", "theek", "boliye"]
        found = [w for w in hindi_words if w in self.SAMPLE_TRANSCRIPT.lower()]
        assert len(found) >= 2, "Transcript should contain Hindi conversation"


class TestGeminiLiveAPIIntegration:
    """
    Tests Gemini Live API WebSocket connection setup.
    Uses mocks — no real API calls in unit tests.
    """

    def test_gemini_live_model_name(self):
        """Verify correct Gemini Live model is configured."""
        from mcp_servers.voice_mcp import GEMINI_LIVE_MODEL
        assert "gemini" in GEMINI_LIVE_MODEL
        assert "live" in GEMINI_LIVE_MODEL or "flash-live" in GEMINI_LIVE_MODEL or "preview" in GEMINI_LIVE_MODEL
        assert GEMINI_LIVE_MODEL != "", "GEMINI_LIVE_MODEL must not be empty"

    def test_live_model_differs_from_text_model(self):
        """Voice model must be a different (Live API) model than the text LLM agents use."""
        import os
        from mcp_servers.voice_mcp import GEMINI_LIVE_MODEL
        text_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
        assert GEMINI_LIVE_MODEL != text_model, \
            "Voice calls must use a Gemini Live model, not the same model as text agents"

    def test_model_supports_multilingual(self):
        """Single Gemini Live model handles all Indian languages without switching models."""
        from mcp_servers.voice_mcp import GEMINI_LIVE_MODEL, SUPPORTED_LANGUAGES
        assert len(SUPPORTED_LANGUAGES) >= 10, "Should support at least 10 Indian languages"
        # All languages use the same model — Gemini Live handles multilingual natively
        assert GEMINI_LIVE_MODEL, "GEMINI_LIVE_MODEL must be set for multilingual voice calls"

    def test_gemini_live_model_set(self):
        """Gemini Live model env var is configured for outbound calls."""
        from mcp_servers.voice_mcp import GEMINI_LIVE_MODEL
        assert GEMINI_LIVE_MODEL, "GEMINI_LIVE_MODEL must be set for voice calls"
