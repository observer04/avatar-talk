"""LiveKit Agents worker: the conversational brain of the digital human.

Run locally:   uv run python -m agent.main dev
In production: deployed on Modal (infra/modal/agent_app.py), started with `start`.

The API dispatches this agent (agent_name=AGENT_NAME) into a room with
SessionMetadata as JSON; the agent resolves the persona, builds its STT/LLM/TTS
from the persona config, attaches the avatar backend and starts talking.
"""

from __future__ import annotations

import logging

from avatar_core import AGENT_NAME
from avatar_core.logging import bind, setup_logging
from avatar_core.telemetry import setup_telemetry, shutdown_telemetry
from livekit.agents import AgentServer, AgentSession, JobContext, JobProcess, TurnHandlingOptions, cli, inference
from livekit.agents.telemetry import set_tracer_provider
from livekit.plugins import silero

from agent.avatars import attach_with_fallback, get_backend
from agent.observability import SessionObserver
from agent.persona_agent import PersonaAgent
from agent.persona_source import parse_metadata, resolve_persona
from agent.providers import make_llm, make_stt, make_tts, stt_handles_turns
from agent.settings import settings
from agent.tools import load_tools

log = logging.getLogger("agent")


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server = AgentServer(setup_fnc=prewarm, prometheus_port=settings.prometheus_port)


def _turn_handling(persona) -> TurnHandlingOptions:
    # Start the LLM *and* TTS on the STT's early end-of-turn signal; discarded if the user keeps talking.
    preemptive = {"enabled": True, "preemptive_tts": True}
    interruption = {"mode": settings.interruption_mode, "min_duration": settings.interruption_min_duration}
    # Deepgram Flux decides end of turn from acoustic + semantic cues; otherwise LiveKit's
    # audio turn detector (falls back to the local v1-mini model when needed).
    detection = "stt" if stt_handles_turns(persona.stt) else inference.TurnDetector()
    return TurnHandlingOptions(
        turn_detection=detection, preemptive_generation=preemptive, interruption=interruption
    )


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    meta = parse_metadata(ctx.job.metadata)
    persona = await resolve_persona(meta)
    bind(session_id=meta.session_id, room=ctx.room.name, persona_id=persona.id)

    provider = setup_telemetry(settings)
    set_tracer_provider(
        provider,
        metadata={
            "langfuse.session.id": meta.session_id,
            "session_id": meta.session_id,
            "persona_id": persona.id,
            "room": ctx.room.name,
        },
        allow_pii=settings.trace_content,
    )

    backend = get_backend(persona)
    log.info(
        "session starting",
        extra={"fields": {"stt": persona.stt.model, "llm": persona.llm.model, "tts": persona.voice.model,
                          "avatar": backend.name}},
    )

    session = AgentSession(
        stt=make_stt(persona.stt),
        llm=make_llm(persona.llm),
        tts=make_tts(persona.voice),
        vad=ctx.proc.userdata["vad"],
        turn_handling=_turn_handling(persona),
    )
    observer = SessionObserver(session, persona, meta, backend.name)

    async def on_shutdown() -> None:
        await observer.report()
        await backend.aclose()
        shutdown_telemetry()

    ctx.add_shutdown_callback(on_shutdown)

    await ctx.connect()
    video_active = await attach_with_fallback(backend, session, ctx.room, persona)
    agent = PersonaAgent(
        persona,
        tools=load_tools(persona.tools),
        publish_cues=backend.wants_cues,
    )
    await session.start(agent=agent, room=ctx.room)
    log.info("session live", extra={"fields": {"video_avatar": video_active}})


def run() -> None:
    # LiveKit's CLI installs its own log handler (JSON in production with --log-format json);
    # we only add correlation fields (session_id, room, persona_id, service) to every record.
    setup_logging(settings.service_name, settings.log_level, install_handler=False)
    cli.run_app(server)


if __name__ == "__main__":
    run()
