"""Шлюз модели: каждый сбой даёт честную ошибку, а не данные (моки HTTP через respx).

Проверяем таблицу из docs/spec/09-model-gateway.md: таймаут, 503, 429, 401, не-JSON,
finish_reason=length, ответ не по схеме, неподдержанный response_format, <think> в ответе,
а также что в запросе выключен thinking и нет шаблонов в схеме.
"""

from __future__ import annotations

import io
import json
from typing import Any

import httpx
import pytest
import respx
from PIL import Image

from app.config import APP_ROOT, Settings
from app.llm.errors import ModelError
from app.llm.gateway import Gateway
from app.llm.schema_builder import extract_schema
from app.registry.loader import Registry, load_registry

LLM = "http://llm.test/v1"
OCR = "http://ocr.test"


@pytest.fixture(scope="module")
def registry() -> Registry:
    return load_registry(APP_ROOT / "registry" / "documents.yaml")


@pytest.fixture
def gateway(registry: Registry) -> Gateway:
    settings = Settings(LLM_BASE_URL=LLM, LLM_MODEL="test-model", OCR_BASE_URL=OCR)  # type: ignore[call-arg]
    return Gateway(settings, registry)


def png(size: tuple[int, int] = (64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, "PNG")
    return buf.getvalue()


def valid_answer(registry: Registry, doc_type: str = "snils") -> dict[str, Any]:
    fields = {
        name: {"raw": None, "status": "absent", "kind": "printed", "bbox": None}
        for name in registry.documents[doc_type].fields
    }
    fields["number"] = {"raw": "000-000-000 00", "status": "ok", "kind": "printed", "bbox": [1, 2, 3, 4]}
    return {
        "doc_type": doc_type,
        "page_side": "front",
        "orientation": 0,
        "is_document": True,
        "mrz_lines": [],
        "fields": fields,
    }


def completion(content: str, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


@respx.mock
async def test_success_and_request_shape(gateway: Gateway, registry: Registry) -> None:
    route = respx.post(f"{LLM}/chat/completions").mock(
        return_value=completion(json.dumps(valid_answer(registry)))
    )
    result = await gateway.extract(png(), "snils", None)
    assert result.data["fields"]["number"]["raw"] == "000-000-000 00"
    assert result.base_url_host == "llm.test" and result.inner_retries == 0
    body = json.loads(route.calls[0].request.content)
    assert body["chat_template_kwargs"]["enable_thinking"] is False
    assert body["temperature"] == 0 and body["presence_penalty"] == 0
    assert body["response_format"]["type"] == "json_schema"
    assert body["messages"][1]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_schema_has_no_patterns(registry: Registry) -> None:
    for doc_type in registry.documents:
        text = json.dumps(extract_schema(registry, doc_type))
        assert '"pattern"' not in text and '"format"' not in text and "$ref" not in text


@respx.mock
async def test_timeout_is_retryable(gateway: Gateway) -> None:
    respx.post(f"{LLM}/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ModelError) as e:
        await gateway.extract(png(), "snils", None)
    assert e.value.code == "TIMEOUT" and e.value.retryable


@respx.mock
async def test_503_is_unavailable(gateway: Gateway) -> None:
    respx.post(f"{LLM}/chat/completions").mock(return_value=httpx.Response(503))
    with pytest.raises(ModelError) as e:
        await gateway.extract(png(), "snils", None)
    assert e.value.code == "UNAVAILABLE" and e.value.retryable


@respx.mock
async def test_429_keeps_retry_after(gateway: Gateway) -> None:
    respx.post(f"{LLM}/chat/completions").mock(return_value=httpx.Response(429, headers={"Retry-After": "7"}))
    with pytest.raises(ModelError) as e:
        await gateway.extract(png(), "snils", None)
    assert e.value.code == "RATE_LIMITED" and e.value.retry_after_s == 7


@respx.mock
async def test_401_is_not_retryable(gateway: Gateway) -> None:
    respx.post(f"{LLM}/chat/completions").mock(return_value=httpx.Response(401))
    with pytest.raises(ModelError) as e:
        await gateway.extract(png(), "snils", None)
    assert e.value.code == "UNAVAILABLE" and not e.value.retryable


@respx.mock
async def test_not_json_twice_is_error(gateway: Gateway) -> None:
    route = respx.post(f"{LLM}/chat/completions").mock(return_value=completion("Извините, не могу"))
    with pytest.raises(ModelError) as e:
        await gateway.extract(png(), "snils", None)
    assert e.value.code == "BAD_RESPONSE"
    assert route.call_count == 2  # ровно один немедленный повтор


@respx.mock
async def test_not_json_then_valid_succeeds(gateway: Gateway, registry: Registry) -> None:
    respx.post(f"{LLM}/chat/completions").mock(
        side_effect=[completion("{oops"), completion(json.dumps(valid_answer(registry)))]
    )
    result = await gateway.extract(png(), "snils", None)
    assert result.inner_retries == 1


@respx.mock
async def test_truncated_twice_is_error_with_doubled_tokens(gateway: Gateway) -> None:
    route = respx.post(f"{LLM}/chat/completions").mock(return_value=completion('{"doc_type": "sn', "length"))
    with pytest.raises(ModelError) as e:
        await gateway.extract(png(), "snils", None)
    assert e.value.code == "TRUNCATED"
    tokens = [json.loads(c.request.content)["max_tokens"] for c in route.calls]
    assert tokens == [2048, 4096]


@respx.mock
async def test_schema_invalid_gives_no_data(gateway: Gateway, registry: Registry) -> None:
    bad = valid_answer(registry)
    del bad["fields"]["surname"]  # обязательное поле пропало
    bad["fields"]["number"]["raw"] = "ПЕРСОНАЛЬНОЕ-ЗНАЧЕНИЕ"
    bad["fields"]["number"]["status"] = "maybe"
    respx.post(f"{LLM}/chat/completions").mock(return_value=completion(json.dumps(bad, ensure_ascii=False)))
    with pytest.raises(ModelError) as e:
        await gateway.extract(png(), "snils", None)
    assert e.value.code == "SCHEMA_INVALID"
    assert "ПЕРСОНАЛЬНОЕ" not in e.value.detail  # значения из ответа не попадают в текст ошибки


@respx.mock
async def test_unsupported_response_format_falls_back(gateway: Gateway, registry: Registry) -> None:
    route = respx.post(f"{LLM}/chat/completions").mock(
        side_effect=[
            httpx.Response(400, json={"error": "response_format json_schema is not supported"}),
            completion(json.dumps(valid_answer(registry))),
        ]
    )
    result = await gateway.extract(png(), "snils", None)
    assert result.json_mode == "json_object"
    assert json.loads(route.calls[1].request.content)["response_format"] == {"type": "json_object"}


@respx.mock
async def test_think_block_is_stripped(gateway: Gateway, registry: Registry) -> None:
    content = "<think>рассуждаю...</think>\n" + json.dumps(valid_answer(registry))
    respx.post(f"{LLM}/chat/completions").mock(return_value=completion(content))
    result = await gateway.extract(png(), "snils", None)
    assert "<think>" not in result.raw_text


@respx.mock
async def test_large_image_is_downscaled(gateway: Gateway, registry: Registry) -> None:
    route = respx.post(f"{LLM}/chat/completions").mock(
        return_value=completion(json.dumps(valid_answer(registry)))
    )
    await gateway.extract(png((3000, 2000)), "snils", None)
    import base64

    url = json.loads(route.calls[0].request.content)["messages"][1]["content"][0]["image_url"]["url"]
    with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as im:
        assert max(im.size) == 1600


@respx.mock
async def test_arbiter_schema(gateway: Gateway) -> None:
    respx.post(f"{LLM}/chat/completions").mock(return_value=completion('{"text": "4509"}'))
    result = await gateway.arbitrate(png(), "Серия", "4 цифры")
    assert result.data == {"text": "4509"}


@respx.mock
async def test_ocr_ok_and_neutral_filename(gateway: Gateway) -> None:
    route = respx.post(f"{OCR}/ocr").mock(return_value=httpx.Response(200, text="# Текст"))
    result = await gateway.ocr(png())
    assert result.markdown == "# Текст"
    assert b'filename="page.png"' in route.calls[0].request.content


@respx.mock
async def test_ocr_errors(gateway: Gateway) -> None:
    respx.post(f"{OCR}/ocr").mock(side_effect=[httpx.Response(502), httpx.Response(413)])
    with pytest.raises(ModelError) as e1:
        await gateway.ocr(png())
    assert e1.value.retryable
    with pytest.raises(ModelError) as e2:
        await gateway.ocr(png())
    assert not e2.value.retryable
