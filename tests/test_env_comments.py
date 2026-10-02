"""Tests para verificar el control de comentarios humorísticos mediante variables de entorno."""

import os
from decimal import Decimal
from unittest.mock import MagicMock, patch
from src.parser import are_comments_enabled, generar_comentario_ironico


def test_comments_enabled_by_default():
    os.environ.pop("ENABLE_BOT_COMMENTS", None)
    os.environ.pop("BOT_ENABLE_COMMENTS", None)
    assert are_comments_enabled() is True


def test_comments_disabled_via_enable_bot_comments():
    for disable_val in ["false", "False", "0", "no", "off", "disable", "disabled"]:
        os.environ["ENABLE_BOT_COMMENTS"] = disable_val
        assert are_comments_enabled() is False
        assert generar_comentario_ironico(Decimal("5000"), "café", "Alimentación") == ""


def test_comments_disabled_via_bot_enable_comments_fallback():
    os.environ.pop("ENABLE_BOT_COMMENTS", None)
    os.environ["BOT_ENABLE_COMMENTS"] = "false"
    assert are_comments_enabled() is False
    assert generar_comentario_ironico(Decimal("5000"), "café", "Alimentación") == ""


def test_comments_explicitly_enabled():
    os.environ["ENABLE_BOT_COMMENTS"] = "true"
    assert are_comments_enabled() is True
