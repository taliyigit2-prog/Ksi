"""On-demand Ollama HTTP client with explicit model unloading."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request

from ksi_local.network_policy import require_loopback_http_url
from ksi_local.network_policy import open_loopback as urlopen


@dataclass(frozen=True)
class OllamaClient:
    base_url: str = "http://127.0.0.1:11435"
    timeout_seconds: int = 600

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", require_loopback_http_url(self.base_url))
        if self.timeout_seconds <= 0:
            raise ValueError("Ollama zaman aşımı sıfırdan büyük olmalıdır.")

    def _post(self, endpoint: str, payload: dict[str, object]) -> dict[str, object]:
        request = Request(
            f"{self.base_url.rstrip('/')}{endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        from ksi_local.bundle_runtime import bundle_root
        if bundle_root() is not None:
            from ksi_local.owned_ollama import open_owned_loopback
            transport = open_owned_loopback
        else:
            transport = urlopen
        try:
            with transport(request, timeout=self.timeout_seconds) as response:
                content = response.read(4 * 1024**2 + 1)
                if len(content) > 4 * 1024**2:
                    raise RuntimeError("Yerel model API yanıtı boyut sınırını aşıyor.")
                result = json.loads(content.decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as error:
            raise RuntimeError(f"Ollama isteği başarısız: {error}") from error
        if not isinstance(result, dict):
            raise RuntimeError("Ollama beklenen JSON yanıtını döndürmedi.")
        if result.get("error"):
            raise RuntimeError(f"Ollama: {result['error']}")
        return result

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        system: str | None = None,
        json_mode: bool = False,
        json_schema: dict[str, object] | None = None,
        temperature: float = 0.1,
        keep_alive: int | str = 0,
        max_tokens: int | None = None,
    ) -> str:
        from ksi_local.bundle_runtime import bundle_root
        if bundle_root() is not None and model not in {"translategemma:4b-it-q8_0", "qwen3.5:4b"}:
            raise ValueError("Paketlenmiş uygulama yalnız doğrulanmış yerel model etiketlerini kullanabilir.")
        if max_tokens is not None and not 1 <= max_tokens <= 4096:
            raise ValueError("Model çıktı sınırı 1–4096 token arasında olmalıdır.")
        if json_mode and json_schema is not None:
            raise ValueError("JSON modu ile JSON şeması aynı anda kullanılamaz.")
        options: dict[str, object] = {"temperature": temperature, "num_ctx": 8192}
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        payload: dict[str, object] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": keep_alive,
            "think": False,
            "options": options,
        }
        if system:
            payload["system"] = system
        if json_schema is not None:
            payload["format"] = json_schema
        elif json_mode:
            payload["format"] = "json"
        result = self._post("/api/generate", payload)
        response = result.get("response")
        if not isinstance(response, str) or not response.strip():
            raise RuntimeError("Ollama boş metin döndürdü.")
        return response.strip()
