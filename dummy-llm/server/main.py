import time
import uuid
import json
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="Dummy LLM Server", version="1.0.0")


@app.middleware("http")
async def log_request_body(request: Request, call_next):
    body = await request.body()
    if body:
        try:
            parsed = json.loads(body)
            print(f"\n=== REQUEST {request.method} {request.url.path} ===")
            print(json.dumps(parsed, ensure_ascii=False, indent=2))
            print("=" * 50)
        except json.JSONDecodeError:
            print(f"\n=== REQUEST {request.method} {request.url.path} (raw) ===")
            print(body.decode(errors="replace"))
            print("=" * 50)
    else:
        print(f"\n=== REQUEST {request.method} {request.url.path} (no body) ===")
    return await call_next(request)


# ── リクエストモデル ──────────────────────────────────────────────────────────

class Message(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "dummy-model"
    messages: list[Message]
    stream: bool = False
    temperature: float = 1.0
    max_tokens: int | None = None


# ── レスポンス生成ヘルパー ────────────────────────────────────────────────────

def _make_reply(request: ChatCompletionRequest) -> str:
    for msg in reversed(request.messages):
        if msg.role == "user":
            return f"なるほど、「{msg.content.strip()}」、そのとおりですね。"
    return "なるほど、そのとおりですね。"


def _non_stream_response(request: ChatCompletionRequest) -> dict:
    reply = _make_reply(request)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": reply},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": sum(len(m.content.split()) for m in request.messages),
            "completion_tokens": len(reply.split()),
            "total_tokens": sum(len(m.content.split()) for m in request.messages)
            + len(reply.split()),
        },
    }


async def _stream_generator(request: ChatCompletionRequest) -> AsyncGenerator[str, None]:
    """OpenAI 互換の SSE チャンクを yield する。"""
    reply = _make_reply(request)
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    # テキストを単語単位で分割して少しずつ送る（ストリーミングらしさを演出）
    words = reply.split(" ")
    for i, word in enumerate(words):
        chunk_content = word if i == 0 else f" {word}"
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": chunk_content},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(chunk)}\n\n"

    # 終端チャンク
    end_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }
        ],
    }
    yield f"data: {json.dumps(end_chunk)}\n\n"
    yield "data: [DONE]\n\n"


# ── エンドポイント ────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models():
    """Dify がモデル一覧を確認する際に呼ばれる場合があるため実装しておく。"""
    return {
        "object": "list",
        "data": [
            {
                "id": "dummy-model",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "dummy",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    if request.stream:
        return StreamingResponse(
            _stream_generator(request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    return JSONResponse(content=_non_stream_response(request))
