from fast_langdetect import detect

from karaoke_he.languages import LANGUAGES

# Confidence below this is treated as "don't know" rather than risking a wrong auto-selection -
# e.g. very short or mixed-script lyrics snippets can give a low-confidence guess that's better
# left to the user to pick manually.
MIN_CONFIDENCE = 0.5

_BCP47_TO_KEY = {config.bcp47: key for key, config in LANGUAGES.items()}


def detect_language(text: str) -> str | None:
    """Best-effort guess of which of `languages.LANGUAGES` the given lyrics text is written in,
    via fastText's lid.176 model (the compressed "lite" variant - a few hundred KB, downloaded
    once and cached, versus the ~125MB full model - accurate enough for a language guess on a
    full page of lyrics). Returns one of `LANGUAGES`'s keys, or `None` if detection failed, was
    low-confidence, or the detected language isn't one of the ones this pipeline supports (in
    which case the user is expected to pick a language manually)."""
    text = text.strip().replace("\n", " ")
    if not text:
        return None

    try:
        results = detect(text, model="lite")
    except Exception:
        return None
    if not results:
        return None

    best = results[0]
    if best["score"] < MIN_CONFIDENCE:
        return None
    return _BCP47_TO_KEY.get(best["lang"])
