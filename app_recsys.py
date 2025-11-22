
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

import sys
import traceback
from datetime import datetime
from http import HTTPStatus
from typing import List, Dict, Tuple

from aiohttp import web
from aiohttp.web import Request, Response, json_response
from botbuilder.core import TurnContext
from botbuilder.core.integration import aiohttp_error_middleware
from botbuilder.integration.aiohttp import CloudAdapter, ConfigurationBotFrameworkAuthentication
from botbuilder.schema import Activity, ActivityTypes

# ✅ Azure Text Analytics imports
from azure.ai.textanalytics import TextAnalyticsClient
from azure.core.credentials import AzureKeyCredential

from bots import EchoBot
from config import DefaultConfig

# ✅ Load configuration and initialize Azure Text Analytics
CONFIG = DefaultConfig()

# Align with your config.py naming
AI_KEY = CONFIG.API_Key
ENDPOINT_URI = CONFIG.ENDPOINT_URI

if not AI_KEY or not ENDPOINT_URI:
    print("⚠️  Missing Azure Text Analytics credentials. Please set environment variables:")
    print("   MicrosoftAPIKey and MicrosoftAIServiceEndpoint")

credential = AzureKeyCredential(AI_KEY)
text_analytics_client = TextAnalyticsClient(endpoint=ENDPOINT_URI, credential=credential)

# Create adapter
ADAPTER = CloudAdapter(ConfigurationBotFrameworkAuthentication(CONFIG))


# -----------------------------
# Simple Content-Based Recommender (Demo)
# -----------------------------

# A tiny demo catalog. In production, pull this from a DB.
CATALOG: List[Dict] = [
    {
        "id": "C101",
        "title": "Intro to Generative AI",
        "tags": {"ai", "machine learning", "nlp", "text generation", "deep learning"},
        "description": "Foundations of large language models, prompt design, and safety basics.",
        "url": "https://example.com/courses/genai-intro"
    },
    {
        "id": "C205",
        "title": "Project Scheduling for Construction",
        "tags": {"construction", "scheduling", "project management", "critical path", "resources"},
        "description": "Plan 3-week lookaheads, manage resources, and track earned value in the field.",
        "url": "https://example.com/courses/construction-scheduling"
    },
    {
        "id": "A301",
        "title": "Key Phrase Extraction with Azure",
        "tags": {"azure", "text analytics", "key phrases", "nlp", "pipelines"},
        "description": "How to extract key phrases and leverage them for search and recommendations.",
        "url": "https://example.com/articles/azure-keyphrases"
    },
    {
        "id": "T112",
        "title": "Thermal Loads & HVAC Controls",
        "tags": {"hvac", "controls", "chilled water", "data centers", "energy efficiency"},
        "description": "Control strategies for CRAH units, chilled water loops, and generator exhaust.",
        "url": "https://example.com/training/hvac-controls"
    },
    {
        "id": "D410",
        "title": "Designing Conversational Agents",
        "tags": {"chatbots", "dialog", "nlp", "ux", "conversation design"},
        "description": "Turn intents and entities into helpful dialogue flows and evaluate CUX.",
        "url": "https://example.com/courses/conversation-design"
    },
]

def _normalize_tokens(tokens: List[str]) -> List[str]:
    return [t.lower().strip() for t in tokens if isinstance(t, str) and t.strip()]

def _tokenize(text: str) -> List[str]:
    # naive tokenizer to keep dependencies minimal
    import re
    return re.findall(r"[a-zA-Z][a-zA-Z0-9\-\_]+", text.lower())

def jaccard(a: List[str], b: List[str]) -> float:
    set_a, set_b = set(a), set(b)
    if not set_a and not set_b:
        return 0.0
    return len(set_a & set_b) / float(len(set_a | set_b))

def extract_key_phrases(text: str) -> List[str]:
    documents = [{"id": "1", "language": "en", "text": text}]
    result = text_analytics_client.extract_key_phrases(documents)
    phrases: List[str] = []
    for doc in result:
        if not doc.is_error:
            phrases.extend(doc.key_phrases)
    return _normalize_tokens(phrases)

def analyze_sentiment(text: str) -> Tuple[str, Dict[str, float]]:
    documents = [{"id": "1", "language": "en", "text": text}]
    response = text_analytics_client.analyze_sentiment(documents)
    for doc in response:
        if not doc.is_error:
            return (
                doc.sentiment,
                {
                    "positive": float(doc.confidence_scores.positive),
                    "negative": float(doc.confidence_scores.negative),
                    "neutral": float(doc.confidence_scores.neutral),
                },
            )
    # fallback
    return ("neutral", {"positive": 0.33, "negative": 0.33, "neutral": 0.34})

def score_item(user_tokens: List[str], item: Dict) -> float:
    # Build item token set from tags + description
    item_tokens = set()
    item_tokens |= set(_normalize_tokens(list(item.get("tags", set()))))
    item_tokens |= set(_tokenize(item.get("description", "")))
    return jaccard(user_tokens, list(item_tokens))

def recommend_items(user_text: str, k: int = 3) -> List[Dict]:
    # Use Azure TA to get key phrases; fallback to tokens if empty
    phrases = extract_key_phrases(user_text)
    tokens = phrases if phrases else _tokenize(user_text)
    # Score catalog
    scored = [(item, score_item(tokens, item)) for item in CATALOG]
    # Filter extremely low matches to avoid noisy recs
    scored = [(it, sc) for it, sc in scored if sc > 0.01]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [it for it, _ in scored[:k]] or CATALOG[:k]

# -----------------------------
# Error handling for the Bot Adapter
# -----------------------------
async def on_error(context: TurnContext, error: Exception):
    print(f"\\n [on_turn_error] unhandled error: {error}", file=sys.stderr)
    traceback.print_exc()

    await context.send_activity("The bot encountered an error or bug.")
    await context.send_activity("To continue to run this bot, please fix the bot source code.")
    if context.activity.channel_id == "emulator":
        trace_activity = Activity(
            label="TurnError",
            name="on_turn_error Trace",
            timestamp=datetime.utcnow(),
            type=ActivityTypes.trace,
            value=f"{error}",
            value_type="https://www.botframework.com/schemas/error",
        )
        await context.send_activity(trace_activity)


ADAPTER.on_turn_error = on_error
BOT = EchoBot()


# -----------------------------
# HTTP Handlers
# -----------------------------

# Listen for incoming requests on /api/messages
async def messages(req: Request) -> Response:
    if "application/json" not in req.headers.get("Content-Type", ""):
        return Response(status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE)

    body = await req.json()
    print("Incoming body:", body)

    # Handle only if Activity payload has "text" field or raw JSON has "text"
    text_payload = None
    if isinstance(body, dict) and "text" in body and isinstance(body["text"], str):
        text_payload = body["text"]

    # ✅ Run Sentiment + Recommendations when text present
    if text_payload:
        sentiment_label, scores = analyze_sentiment(text_payload)
        recs = recommend_items(text_payload, k=3)

        # Build a human-friendly message appended to the original text
        rec_lines = []
        for idx, r in enumerate(recs, 1):
            rec_lines.append(f"{idx}. {r['title']} — {r['url']}")

        augmented_text = (
            f"Sentiment: {sentiment_label} | "
            f"Positive: {scores['positive']:.2f}, Negative: {scores['negative']:.2f}, Neutral: {scores['neutral']:.2f}\\n"
            f"Top recommendations based on your message:\\n" + "\\n".join(rec_lines)
        )

        # Put augmented text back so the EchoBot can surface it
        body["text"] = augmented_text

    activity = Activity().deserialize(body)
    auth_header = req.headers.get("Authorization", "")

    response = await ADAPTER.process_activity(auth_header, activity, BOT.on_turn)

    if response and hasattr(response, "body"):
        return json_response(data=response.body, status=response.status)

    return Response(status=HTTPStatus.OK)


# Optional: a direct endpoint for recommendations (bypass Bot Framework)
async def recommend(req: Request) -> Response:
    if "application/json" not in req.headers.get("Content-Type", ""):
        return Response(status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
    body = await req.json()
    text = body.get("text", "")
    if not isinstance(text, str) or not text.strip():
        return json_response({"error": "Provide a JSON body with a non-empty 'text' field."}, status=HTTPStatus.BAD_REQUEST)

    sentiment_label, scores = analyze_sentiment(text)
    recs = recommend_items(text, k=5)
    return json_response({
        "sentiment": sentiment_label,
        "scores": scores,
        "recommendations": recs
    })

APP = web.Application(middlewares=[aiohttp_error_middleware])
APP.router.add_post("/api/messages", messages)
APP.router.add_post("/api/recommend", recommend)  # new

if __name__ == "__main__":
    try:
        web.run_app(APP, host="localhost", port=CONFIG.PORT)
    except Exception as error:
        raise error
