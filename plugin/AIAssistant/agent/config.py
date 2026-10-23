# Plugin settings live in the slicer's own preferences file (steslicer.cfg),
# never in the source code. The chat page edits them through /api/config.
import os
from collections import OrderedDict

from UM.Application import Application

PREFIX = "ai_assistant/"

DEFAULTS = OrderedDict([
    ("llm_url", ""),          # e.g. https://host/v1
    ("llm_key", ""),
    ("llm_model", ""),
    ("ssl_verify", True),
    ("temperature", 0.2),
    ("max_steps", 30),        # tool-call rounds per user message
    ("results_dir", ""),      # empty = ~/Documents/STE AI Assistant/results
    ("dev_mode", False),      # enables /api/debug/eval
    ("compact_history", True),  # shorten old tool results / drop resent images (see Session._request_messages)
    ("confirm_plan", True),
    ("slicer_popups", True),  # also show the plan card and notifications inside the slicer (slicer_ui.py)   # show apply_plan as a card and wait for "Применить" (see Session._confirm_plan)
])


def _prefs():
    return Application.getInstance().getPreferences()


def init():
    prefs = _prefs()
    for key, default in DEFAULTS.items():
        prefs.addPreference(PREFIX + key, default)


def get(key):
    value = _prefs().getValue(PREFIX + key)
    default = DEFAULTS.get(key)
    if value is None:
        return default
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(default, (int, float)) and not isinstance(default, bool):
        try:
            return type(default)(float(value))
        except (TypeError, ValueError):
            return default
    return value


def set_many(values):
    prefs = _prefs()
    for key, value in values.items():
        if key in DEFAULTS:
            prefs.setValue(PREFIX + key, value)
    try:
        Application.getInstance().savePreferences()
    except Exception:
        pass


def public():
    """Config as shown to the browser: the key is never sent back in full."""
    key = get("llm_key") or ""
    masked = ""
    if key:
        masked = (key[:4] + "…" + key[-4:]) if len(key) > 10 else "••••"
    return {
        "llm_url": get("llm_url"),
        "llm_model": get("llm_model"),
        "has_key": bool(key),
        "key_masked": masked,
        "ssl_verify": get("ssl_verify"),
        "temperature": get("temperature"),
        "max_steps": get("max_steps"),
        "results_dir": results_root(),
        "dev_mode": get("dev_mode"),
        "compact_history": get("compact_history"),
        "confirm_plan": get("confirm_plan"),
        "slicer_popups": get("slicer_popups"),
    }


def results_root():
    custom = get("results_dir")
    if custom:
        return custom
    return os.path.join(os.path.expanduser("~"), "Documents", "STE AI Assistant", "results")
