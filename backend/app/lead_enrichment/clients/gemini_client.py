import json
from typing import Any

from google import genai
from google.genai import types

from app.lead_enrichment.config import EnrichmentSettings
from app.lead_enrichment.models import LeadClassification


CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "industry": {"type": "string"},
        "segment": {"type": "string"},
        "pricing_model": {"type": "string"},
        "compliance_posture": {"type": "string"},
        "procurement_signals": {"type": "array", "items": {"type": "string"}},
        "outreach_triggers": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "fit_score": {"type": "integer"},
        "urgency_score": {"type": "integer"},
        "confidence_score": {"type": "integer"},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "detail": {"type": "string"},
                    "source_url": {"type": "string"},
                },
                "required": ["label", "detail", "source_url"],
            },
        },
    },
    "required": [
        "industry",
        "segment",
        "pricing_model",
        "compliance_posture",
        "procurement_signals",
        "outreach_triggers",
        "risks",
        "fit_score",
        "urgency_score",
        "confidence_score",
        "evidence",
    ],
}


class GeminiClient:
    def __init__(self, settings: EnrichmentSettings) -> None:
        self.settings = settings
        self.client = genai.Client(api_key=settings.gemini_api_key)

    async def classify(self, prompt: str) -> LeadClassification:
        response = await self.client.aio.models.generate_content(
            model=self.settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CLASSIFICATION_SCHEMA,
                temperature=0.2,
            ),
        )
        payload = response.text or "{}"
        return LeadClassification.model_validate(json.loads(payload))

