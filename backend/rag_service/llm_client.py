"""
NeuraFleet RAG LLM Client
==========================

Interfaces with the Anthropic API (Claude) to generate natural-language
responses grounded in retrieved fleet documentation context.

When the ANTHROPIC_API_KEY is not set, falls back to a rule-based
response engine that extracts keywords from the query and matches
them against the provided context, suitable for demo purposes.
"""

from __future__ import annotations

import logging
import os
import re
from typing import List, Optional

logger = logging.getLogger("rag_service.llm_client")


class LLMClient:
    """
    Generate RAG responses using Claude or a local fallback.
    """

    SYSTEM_PROMPT = (
        "You are NeuraFleet Assistant, an expert AI helper for a cloud-native "
        "robot fleet simulation platform. You help operators understand robot "
        "status, troubleshoot issues, interpret sensor data, and follow "
        "maintenance and safety procedures.\n\n"
        "Answer concisely and accurately using ONLY the provided context "
        "documents. If the context does not contain enough information, say so "
        "clearly rather than guessing.\n\n"
        "When referring to specific robots, use their ID and name."
    )

    def __init__(self):
        self._api_key = os.getenv("ANTHROPIC_API_KEY")
        self._model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        self._client = None

        if self._api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self._api_key)
                logger.info("Anthropic client initialised (model=%s)", self._model)
            except Exception as exc:
                logger.warning("Failed to initialise Anthropic client: %s", exc)
        else:
            logger.info(
                "ANTHROPIC_API_KEY not set; using rule-based fallback responses."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_response(
        self,
        query: str,
        context: List[str],
        robot_id: Optional[str] = None,
    ) -> str:
        """
        Generate a response to the user's query grounded in *context*.

        Parameters
        ----------
        query : str
            The user's natural-language question.
        context : list[str]
            Retrieved document chunks from the vector store.
        robot_id : str, optional
            If provided, the response is scoped to this robot.

        Returns
        -------
        str
            The generated answer.
        """
        if self._client is not None:
            return await self._call_anthropic(query, context, robot_id)
        return self._rule_based_response(query, context, robot_id)

    # ------------------------------------------------------------------
    # Anthropic API path
    # ------------------------------------------------------------------

    async def _call_anthropic(
        self,
        query: str,
        context: List[str],
        robot_id: Optional[str],
    ) -> str:
        context_block = "\n\n---\n\n".join(context) if context else "(no context available)"
        user_msg = (
            f"Context documents:\n{context_block}\n\n"
            f"{'Robot ID: ' + robot_id + chr(10) if robot_id else ''}"
            f"Question: {query}"
        )

        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=self.SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            return message.content[0].text
        except Exception as exc:
            logger.error("Anthropic API call failed: %s", exc)
            return self._rule_based_response(query, context, robot_id)

    # ------------------------------------------------------------------
    # Rule-based fallback (demo mode)
    # ------------------------------------------------------------------

    def _rule_based_response(
        self,
        query: str,
        context: List[str],
        robot_id: Optional[str],
    ) -> str:
        """
        Analyse query keywords and the retrieved context to produce a
        helpful response without calling an external LLM.
        """
        q = query.lower()
        scope = f" for robot {robot_id}" if robot_id else ""

        # Collect relevant context snippets (first 3 that seem useful)
        relevant_snippets = self._extract_relevant_snippets(q, context, max_snippets=3)

        # Keyword-based response templates
        if any(kw in q for kw in ("temperature", "heat", "thermal", "overheat", "hot")):
            base = (
                f"Regarding temperature concerns{scope}: "
                "High temperature readings (above 75°C) typically indicate heavy "
                "computational load, environmental heat exposure, or cooling system "
                "degradation. Recommended actions include reducing task load, checking "
                "ventilation, and inspecting cooling fans."
            )
        elif any(kw in q for kw in ("battery", "charge", "power", "drain")):
            base = (
                f"Regarding battery/power{scope}: "
                "Battery levels below 20% trigger a low-battery alert. Robots "
                "automatically navigate to their assigned charging station when "
                "battery drops below 15%. Normal drain rate is approximately "
                "0.6% per minute during active operation. Hauler-type robots "
                "consume about 50% more power due to heavier payloads."
            )
        elif any(kw in q for kw in ("sensor", "imu", "gps", "lidar", "calibrat")):
            base = (
                f"Regarding sensor systems{scope}: "
                "The fleet robots are equipped with a VLP-16 LiDAR (100 m range, "
                "±3 cm accuracy, 10 Hz scan rate), a 6-axis IMU (1 kHz sample rate), "
                "and dual-band GPS (±2 m horizontal accuracy). Sensor calibration "
                "should be performed every 500 operating hours or after a collision event."
            )
        elif any(kw in q for kw in ("maintenance", "inspect", "firmware", "update", "replace")):
            base = (
                f"Regarding maintenance procedures{scope}: "
                "Routine maintenance includes battery health checks every 30 days, "
                "sensor calibration every 500 hours, motor inspection every 1000 hours, "
                "and firmware updates as released. Always follow the lock-out/tag-out "
                "procedure before physical maintenance."
            )
        elif any(kw in q for kw in ("safety", "emergency", "stop", "collision", "geofence")):
            base = (
                f"Regarding safety protocols{scope}: "
                "Emergency stop can be triggered via the physical E-Stop button, the "
                "fleet management console, or voice command. The collision avoidance "
                "system uses a three-zone approach: awareness (5 m), warning (2 m), and "
                "critical (0.5 m). Geofencing boundaries are enforced by the navigation "
                "system with a 1 m buffer zone."
            )
        elif any(kw in q for kw in ("troubleshoot", "error", "problem", "issue", "fix", "stall")):
            base = (
                f"Regarding troubleshooting{scope}: "
                "Common issues include sensor drift (recalibrate via the diagnostics menu), "
                "communication loss (check antenna connections and reboot the network module), "
                "and motor stalls (clear obstructions and run the motor test sequence). "
                "Error codes are documented in the maintenance guide."
            )
        elif any(kw in q for kw in ("status", "fleet", "robot", "overview")):
            base = (
                f"Fleet overview{scope}: "
                "The fleet consists of six robots: Atlas-1 (explorer), Scout-2 (scout), "
                "Hauler-3 (hauler), Sentinel-4 (sentinel), Mapper-5 (mapper), and "
                "Relay-6 (relay). Each robot type is optimised for specific tasks. "
                "Check the telemetry dashboard for real-time status."
            )
        elif any(kw in q for kw in ("start", "boot", "startup", "shutdown", "power on")):
            base = (
                f"Regarding startup/shutdown{scope}: "
                "Startup sequence: (1) Pre-flight check all sensor connections, "
                "(2) Power on via the main switch, (3) Wait for self-test completion "
                "(~30 seconds), (4) Verify green status LED, (5) Issue start command "
                "from fleet console. Shutdown: Issue stop command, wait for full halt, "
                "then power off."
            )
        else:
            base = (
                f"Regarding your query '{query}'{scope}: "
                "Based on the fleet knowledge base, I can help with robot operations, "
                "sensor specifications, maintenance procedures, troubleshooting, and "
                "safety protocols. Please refine your question for more specific guidance."
            )

        # Append relevant context snippets
        if relevant_snippets:
            base += "\n\nRelevant information from documentation:\n"
            for snippet in relevant_snippets:
                # Trim to ~200 chars
                trimmed = snippet[:200].strip()
                if len(snippet) > 200:
                    trimmed += "..."
                base += f"- {trimmed}\n"

        return base

    @staticmethod
    def _extract_relevant_snippets(
        query_lower: str,
        context: List[str],
        max_snippets: int = 3,
    ) -> List[str]:
        """Pick the most relevant context snippets by keyword overlap."""
        query_words = set(re.findall(r"\w+", query_lower))
        scored: list = []
        for chunk in context:
            chunk_words = set(re.findall(r"\w+", chunk.lower()))
            overlap = len(query_words & chunk_words)
            scored.append((overlap, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [text for _, text in scored[:max_snippets] if text.strip()]
