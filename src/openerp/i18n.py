from __future__ import annotations

import gettext
from pathlib import Path

from openerp.config import get_settings

_TRANSLATIONS: dict[str, gettext.NullTranslations] = {}
_DEFAULT_DOMAIN = "messages"


def _locales_dir() -> Path:
    settings = get_settings()
    return Path(settings.locales_dir)


def get_translation(locale: str) -> gettext.NullTranslations:
    """Return a GNUTranslations for `locale`, falling back to NullTranslations."""
    if locale in _TRANSLATIONS:
        return _TRANSLATIONS[locale]

    locales_dir = _locales_dir()
    try:
        translation = gettext.translation(
            _DEFAULT_DOMAIN, localedir=str(locales_dir), languages=[locale]
        )
    except (FileNotFoundError, OSError):
        translation = gettext.NullTranslations()
    _TRANSLATIONS[locale] = translation
    return translation


def gettext_for(locale: str):
    """Return a callable _(text) bound to the given locale."""
    t = get_translation(locale)
    return t.gettext


def supported_locales() -> tuple[str, ...]:
    return get_settings().available_locales


def normalize_locale(locale: str | None) -> str:
    """Normalize an Accept-Language / cookie value to one of the supported locales."""
    settings = get_settings()
    if not locale:
        return settings.default_locale
    short = locale.replace("-", "_").split("_")[0].lower()
    if short in settings.available_locales:
        return short
    return settings.default_locale


def compile_translation_catalogs() -> int:
    """Compile gettext .po files into .mo catalogs. Returns the number compiled."""
    from babel.messages.mofile import write_mo
    from babel.messages.pofile import read_po

    locales_dir = _locales_dir()
    if not locales_dir.exists():
        return 0

    count = 0
    for po_path in locales_dir.glob("*/LC_MESSAGES/messages.po"):
        with open(po_path, "rb") as f:
            catalog = read_po(f)
        mo_path = po_path.with_suffix(".mo")
        with open(mo_path, "wb") as f:
            write_mo(f, catalog)
        count += 1
        _TRANSLATIONS.pop(po_path.parent.parent.name, None)
    return count
