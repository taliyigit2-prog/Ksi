# Model licenses

Model weights are not covered by the KSI Local Studio Apache-2.0 source license
and must not be committed to the public repository.

Current local model families include TranslateGemma, Qwen, MLX Whisper and
Chatterbox Multilingual. The model manager must show the upstream model card,
license or terms, version, digest, disk size and intended-use restrictions
before installation. Under the accepted October 2026 scope, required weights
must be present on the offline installation media with verified redistribution
terms and notices. First-run downloads cannot substitute for that requirement.
The source repository remains model-free.

CPU speech candidate: Turkish Fettah for Piper. The upstream voice repository
declares MIT; the specific model card identifies a CC0 dataset and notes
fine-tuning from Lessac. Preserve both notices and verify the exact pinned model
revision before packaging. This is a different voice from the accepted
Chatterbox profile, not a claim of equivalent quality or voice cloning.
Sources: [voice repository](https://huggingface.co/rhasspy/piper-voices/tree/v1.0.0),
[Fettah model card](https://huggingface.co/rhasspy/piper-voices/raw/v1.0.0/tr/tr_TR/fettah/medium/MODEL_CARD).

U²-NetP and every Argos language pair also need weight-specific notices. Engine
licenses alone do not establish permission to redistribute their models.
