from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageConfig:
    label: str
    iso639_3: str  # ctc-forced-aligner / MMS language code
    bcp47: str  # mkvmerge track language tag
    strip_nikud: bool = False  # Hebrew-only orthography normalization, see text_utils.py
    arabic_shaping: bool = False  # needs arabic_reshaper before bidi reordering, see subtitle_render.py
    is_rtl: bool = False  # reading direction, see subtitle_render.render_line_frame's chunk order


LANGUAGES: dict[str, LanguageConfig] = {
    "heb": LanguageConfig(label="Hebrew", iso639_3="heb", bcp47="he", strip_nikud=True, is_rtl=True),
    "eng": LanguageConfig(label="English", iso639_3="eng", bcp47="en"),
    "fra": LanguageConfig(label="French", iso639_3="fra", bcp47="fr"),
    "spa": LanguageConfig(label="Spanish", iso639_3="spa", bcp47="es"),
    "rus": LanguageConfig(label="Russian", iso639_3="rus", bcp47="ru"),
    "ara": LanguageConfig(label="Arabic", iso639_3="ara", bcp47="ar", arabic_shaping=True, is_rtl=True),
}

DEFAULT_LANGUAGE = "heb"
