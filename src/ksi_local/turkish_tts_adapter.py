"""Avoid unused upstream Chinese model downloads in Turkish-only narration.

This does not alter Turkish tokenization, weights or audio generation. It is
scoped to one dedicated inference worker and the explicitly pinned upstream API.
"""

import contextlib


@contextlib.contextmanager
def turkish_tokenizer_scope(module):
    original = module.ChineseCangjieConverter

    class UnsupportedChineseConverter:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, text):
            raise ValueError("Bu yerel anlatıcı yalnız Türkçe seslendirme için yapılandırılmıştır.")

    module.ChineseCangjieConverter = UnsupportedChineseConverter
    try:
        yield
    finally:
        module.ChineseCangjieConverter = original
