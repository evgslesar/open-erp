from __future__ import annotations

from fastapi import Request, Response

from openerp.i18n import gettext_for, normalize_locale

_LANG_COOKIE = "lang"


def select_locale(request: Request) -> str:
    """Pick the locale for a request: cookie > Accept-Language > default."""
    cookie = request.cookies.get(_LANG_COOKIE)
    if cookie:
        return normalize_locale(cookie)
    accept = request.headers.get("accept-language", "")
    return normalize_locale(accept.split(",")[0] if accept else None)


def set_locale_cookie(response: Response, locale: str) -> None:
    response.set_cookie(_LANG_COOKIE, locale, max_age=60 * 60 * 24 * 365, httponly=False)


def translator(locale: str):
    """Return a _(text) callable for templates and views."""
    return gettext_for(locale)
