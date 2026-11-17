import os
from unittest.mock import patch
import logging
from app.config import get_text_model

def test_get_text_model_default():
    with patch.dict(os.environ, {}, clear=True):
        assert get_text_model() == "gemini-2.5-flash"

def test_get_text_model_custom_valid():
    with patch.dict(os.environ, {"GEMINI_TEXT_MODEL": "gemini-2.5-pro"}):
        assert get_text_model() == "gemini-2.5-pro"

def test_get_text_model_fallback_with_warning(caplog):
    with patch.dict(os.environ, {"GEMINI_TEXT_MODEL": "gemini-3.8-flash"}):
        with caplog.at_level(logging.WARNING):
            model = get_text_model()
            assert model == "gemini-2.5-flash"
            assert "gemini-3.8-flash" in caplog.text
            assert "fallback" in caplog.text.lower()
