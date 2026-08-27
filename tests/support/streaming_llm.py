"""Small OpenAI-compatible streaming fixture used for manual browser QA."""

from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import uvicorn


app = FastAPI()


@app.get("/v1/models")
async def models() -> dict:
    return {"data": [
        {"id": "qa-stream-model"},
        {"id": "qa-reasoning-model"},
        {"id": "qa-reasoning-slow"},
    ]}


@app.post("/v1/chat/completions")
async def completions(body: dict):
    prompt = (
        "integrated_multimodal_description: [Shot 1] Live-action, a cyclist waits in gentle rain. "
        "[Shot 2] At 00:03.500, the camera cuts to the cyclist crossing.\n\n"
        "overall_soundscape: Rain and distant traffic.\n\n"
        "non_diegetic_music: N/A"
    )
    if any("Ref2VA" in str(message.get("content", "")) for message in body.get("messages", [])):
        prompt = (
            "subject_definitions:\n<Subject 1> is the person in a rainy street scene.\n\n"
            "summary:\n[reference generation + audio reference] The target follows <Subject 1>.\n\n"
            "retention_analysis:\n<Subject 1>: fully_preserved - identity retained.\n\n"
            "detailed_description:\n[Shot 1] <Subject 1> (S1) walks through rain and says, <d>[English] Keep moving.</d>\n\n"
            "overall_soundscape:\nRain and distant traffic.\n\n"
            "non_diegetic_music:\nN/A"
        )
    if not body.get("stream"):
        return {"choices": [{"message": {"content": prompt}}]}

    async def events():
        await asyncio.sleep(0.35)
        if "reasoning" in str(body.get("model", "")):
            count = 30 if "slow" in str(body.get("model", "")) else 3
            for index in range(count):
                yield f"data: {json.dumps({'choices': [{'delta': {'reasoning_content': f'analysis-{index} '}}]})}\n\n"
                await asyncio.sleep(0.12)
        for token in [prompt[index:index + 12] for index in range(0, len(prompt), 12)]:
            yield f"data: {json.dumps({'choices': [{'delta': {'content': token}}]})}\n\n"
            await asyncio.sleep(0.18)
        usage = {"prompt_tokens": 321, "completion_tokens": len(prompt) // 4, "total_tokens": 321 + len(prompt) // 4}
        yield f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18765, log_level="warning")
