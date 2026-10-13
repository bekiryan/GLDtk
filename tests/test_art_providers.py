"""Image providers (Part 6): request building and response handling, offline."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from art.providers import ImageProvider, ImageProviderError, ProviderError, make_image_provider
from art.providers import gemini_image
from art.providers.fake import FakeImageProvider
from art.providers.gemini_image import GeminiImageProvider, nearest_aspect

FIX = Path(__file__).resolve().parent / "fixtures" / "art"
PNG = (FIX / "sprite.png").read_bytes()


def _image_response(data: bytes = PNG, mime: str = "image/png"):
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "here you go"},
                        {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}},
                    ]
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"totalTokenCount": 10},
    }


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(gemini_image.time, "sleep", lambda s: None)


def _script(monkeypatch, outcomes):
    """Replace the HTTP call: each outcome is a dict (response) or an int (HTTP error)."""
    seen = []

    def fake_post(url, headers, payload, timeout):
        seen.append({"url": url, "headers": headers, "payload": payload})
        out = outcomes[len(seen) - 1]
        if isinstance(out, tuple):
            raise ProviderError(f"HTTP error {out[0]}: {out[1]}", status=out[0])
        return out

    monkeypatch.setattr(gemini_image, "http_json_post", fake_post)
    return seen


def test_registry_and_protocol():
    assert isinstance(make_image_provider("fake"), ImageProvider)
    g = make_image_provider("gemini", api_key="k")
    assert isinstance(g, GeminiImageProvider) and g.model == gemini_image.DEFAULT_MODEL
    assert make_image_provider("gemini", model="m", api_key="k").model == "m"
    with pytest.raises(ValueError):
        make_image_provider("dalle")


def test_build_request_key_in_header_only():
    p = GeminiImageProvider(api_key="SECRET")
    url, headers, payload = p.build_request("draw", size_hint=(80, 48), reference_png=b"\x89PNGxx")
    assert "SECRET" not in url and headers["x-goog-api-key"] == "SECRET"
    assert url.endswith(f"/models/{gemini_image.DEFAULT_MODEL}:generateContent")
    gen = payload["generationConfig"]
    assert gen["responseModalities"] == ["IMAGE"]
    assert gen["imageConfig"]["aspectRatio"] == "16:9"
    parts = payload["contents"][0]["parts"]
    assert parts[0]["text"] == "draw" and parts[1]["inlineData"]["mimeType"] == "image/png"
    assert "SECRET" not in json.dumps(payload)


def test_nearest_aspect():
    assert nearest_aspect((1, 1)) == "1:1"
    assert nearest_aspect((80, 48)) == "16:9"
    assert nearest_aspect((3, 2)) == "3:2"


def test_generate_extracts_png(monkeypatch):
    seen = _script(monkeypatch, [_image_response()])
    p = GeminiImageProvider(api_key="k")
    assert p.generate("x") == PNG
    assert p.model_used == gemini_image.DEFAULT_MODEL and p.calls == 1
    assert p.last_text == "here you go"
    assert len(seen) == 1


def test_generate_converts_jpeg(monkeypatch):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 200, 10)).save(buf, format="JPEG")
    _script(monkeypatch, [_image_response(buf.getvalue(), "image/jpeg")])
    out = GeminiImageProvider(api_key="k").generate("x")
    assert out[:8] == b"\x89PNG\r\n\x1a\n"


def test_no_image_raises(monkeypatch):
    _script(monkeypatch, [{"candidates": [{"content": {"parts": [{"text": "no"}]}, "finishReason": "STOP"}]}])
    with pytest.raises(ImageProviderError, match="no image"):
        GeminiImageProvider(api_key="k").generate("x")


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ImageProviderError, match="API key"):
        GeminiImageProvider().generate("x")


def test_quota_falls_back_then_raises_clear_429(monkeypatch, no_sleep):
    n = 1 + len(gemini_image.FALLBACK_MODELS)
    seen = _script(monkeypatch, [(429, "RESOURCE_EXHAUSTED limit: 0")] * n)
    p = GeminiImageProvider(api_key="k")
    with pytest.raises(ImageProviderError) as exc:
        p.generate("x")
    assert exc.value.status == 429 and "quota" in str(exc.value).lower()
    models = [s["url"].split("/models/")[1].split(":")[0] for s in seen]
    assert models == [gemini_image.DEFAULT_MODEL, *gemini_image.FALLBACK_MODELS]


def test_explicit_model_has_no_fallback(monkeypatch, no_sleep):
    seen = _script(monkeypatch, [(429, "quota")])
    with pytest.raises(ImageProviderError):
        GeminiImageProvider(model="gemini-x", api_key="k").generate("x")
    assert len(seen) == 1


def test_transient_retry_then_success(monkeypatch, no_sleep):
    seen = _script(monkeypatch, [(503, "high demand"), _image_response()])
    p = GeminiImageProvider(api_key="k", transient_retries=1)
    assert p.generate("x") == PNG and len(seen) == 2


def test_image_only_rejected_retries_with_text_and_image(monkeypatch):
    seen = _script(monkeypatch, [(400, "response modalities not supported"), _image_response()])
    assert GeminiImageProvider(api_key="k").generate("x") == PNG
    assert seen[1]["payload"]["generationConfig"]["responseModalities"] == ["TEXT", "IMAGE"]


def test_call_budget(monkeypatch, no_sleep):
    _script(monkeypatch, [(503, "x")] * 10)
    p = GeminiImageProvider(api_key="k", max_calls=2, transient_retries=5)
    with pytest.raises(ImageProviderError):
        p.generate("x")
    assert p.calls == 2


def test_fake_provider_records_calls():
    f = FakeImageProvider()
    theme = f.generate("a pixel-art tile sheet", size_hint=(80, 48), reference_png=b"x")
    sprite = f.generate("a sprite")
    assert theme == (FIX / "grid_theme.png").read_bytes()
    assert sprite == (FIX / "sprite.png").read_bytes()
    assert f.calls[0] == {"prompt": "a pixel-art tile sheet", "size_hint": (80, 48), "has_reference": True}
    cyc = FakeImageProvider([b"a", b"b"])
    assert [cyc.generate("p") for _ in range(3)] == [b"a", b"b", b"a"]
