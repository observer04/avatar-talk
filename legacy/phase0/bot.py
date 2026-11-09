"""One conversation session: WebRTC audio in -> STT -> LLM -> TTS -> WebRTC audio out.

Turn-taking uses Silero VAD + Smart Turn v3 (Pipecat's default stop strategy),
both local ONNX models on CPU. Barge-in (interrupting the bot) is handled by
Pipecat: user speech triggers an InterruptionFrame that flushes LLM/TTS/output.
"""

import random

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

from avatar_tags import AvatarTagProcessor
from config import settings
from services import make_avatar_video, make_llm, make_stt, make_tts
from tools import load_tools, slow_tool_names


FILLERS = ["Let me check.", "One moment.", "Let me look that up.", "Just a sec."]


def build_pipeline(transport, context: LLMContext, slow_tools: set[str]) -> Pipeline:
    system_prompt = settings.persona_file.read_text(encoding="utf-8")

    stt = make_stt(settings)
    llm = make_llm(settings, system_instruction=system_prompt)
    tts = make_tts(settings)
    avatar_video = make_avatar_video(settings)

    @llm.event_handler("on_function_calls_started")
    async def on_function_calls_started(service, function_calls):
        # Speak a short filler while slow tools (network lookups) run.
        if any(fc.function_name in slow_tools for fc in function_calls):
            await tts.queue_frame(TTSSpeakFrame(random.choice(FILLERS), append_to_context=False))

    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    processors = [
        transport.input(),
        stt,
        user_aggregator,
        llm,
        AvatarTagProcessor(),  # strips [mood:..]/[gesture:..] cues -> RTVI messages
        tts,
    ]
    if avatar_video is not None:
        processors.append(avatar_video)  # server-rendered face (later: Simli / self-hosted)
    processors += [transport.output(), assistant_aggregator]
    return Pipeline(processors)


async def run_bot(webrtc_connection: SmallWebRTCConnection) -> None:
    logger.info(
        f"Starting session llm={settings.llm_provider} stt={settings.stt_provider} "
        f"tts={settings.tts_provider} avatar={settings.avatar_mode}"
    )
    server_video = settings.avatar_mode != "client3d"
    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            video_out_enabled=server_video,
            video_out_is_live=server_video,
            video_out_width=512,
            video_out_height=512,
        ),
    )

    tools = load_tools(settings.enabled_tool_modules)
    context = LLMContext(tools=tools)
    pipeline = build_pipeline(transport, context, slow_tool_names(tools))

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        context.add_message({"role": "developer", "content": "The call just connected. Greet the person."})
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()
