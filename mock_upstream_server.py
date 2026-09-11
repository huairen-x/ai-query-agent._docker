"""
Mock LLM upstream server for headroom proxy
Echoes back the messages it received so we can extract compressed content
"""
import json
import os
import time
import uuid
from flask import Flask, request, jsonify

app = Flask(__name__)

HOST = os.environ.get("MOCK_UPSTREAM_HOST", "127.0.0.1")
PORT = int(os.environ.get("MOCK_UPSTREAM_PORT", "9999"))


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    """Echo back the messages received (including compressed by proxy)"""
    body = request.get_json(force=True, silent=True) or {}
    messages = body.get("messages", [])

    return jsonify({
        "id": str(uuid.uuid4()),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "mock-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": json.dumps({
                        "echo": True,
                        "received_messages": messages,
                        "total_messages": len(messages),
                    }, ensure_ascii=False),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": sum(len(m.get("content", "")) // 4 for m in messages),
            "completion_tokens": 10,
            "total_tokens": sum(len(m.get("content", "")) // 4 for m in messages) + 10,
        },
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "mock-upstream"})


if __name__ == "__main__":
    print(f"[MockUpstream] Starting on {HOST}:{PORT}...")
    app.run(host=HOST, port=PORT)