"""
WebSocket Gateway Handler
=========================
Manages WebSocket connections, message routing, backpressure,
and connection lifecycle. This is the entry point for all
real-time client communication.
"""

import asyncio
import logging
import uuid
import time
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from core.models import (
    ClientMessage,
    ClientMessageType,
    ServerMessage,
    ServerMessageType,
)
from core.config import settings
from orchestrator.primary_orchestrator import PrimaryOrchestrator

logger = logging.getLogger(__name__)
router = APIRouter()

# Active connections tracker
active_connections: dict[str, "ConnectionState"] = {}


class ConnectionState:
    """Tracks state for a single WebSocket connection."""

    def __init__(self, ws: WebSocket, session_id: str):
        self.ws = ws
        self.session_id = session_id
        self.connected_at = time.time()
        self.last_activity = time.time()
        self.output_queue: asyncio.Queue[ServerMessage] = asyncio.Queue(
            maxsize=settings.STREAM_BUFFER_HIGH_WATERMARK
        )
        self.orchestrator: Optional[PrimaryOrchestrator] = None
        self._send_task: Optional[asyncio.Task] = None
        self._seq_counter = 0

    def next_seq(self) -> int:
        self._seq_counter += 1
        return self._seq_counter


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Main WebSocket endpoint for HadithiAI Live.
    
    Protocol:
    1. Client connects
    2. Server creates session and sends session_created
    3. Client streams audio/text
    4. Server streams responses (audio/text/images)
    5. Connection persists for entire conversation
    """
    await websocket.accept()
    session_id = str(uuid.uuid4())[:12]
    conn = ConnectionState(websocket, session_id)

    logger.info(
        "WebSocket connected",
        extra={"session_id": session_id, "event": "ws_connect"},
    )

    try:
        # ── Initialize orchestrator ──
        app = websocket.app
        conn.orchestrator = PrimaryOrchestrator(
            session_id=session_id,
            firestore=app.state.firestore,
            gemini_pool=app.state.gemini_pool,
            output_queue=conn.output_queue,
        )
        await conn.orchestrator.initialize()

        # ── Register connection ──
        active_connections[session_id] = conn

        # ── Send session confirmation ──
        await _send_message(
            conn,
            ServerMessage(
                type=ServerMessageType.SESSION_CREATED,
                session_id=session_id,
            ),
        )

        # ── Start output sender (runs concurrently) ──
        conn._send_task = asyncio.create_task(
            _output_sender(conn), name=f"sender-{session_id}"
        )

        # ── Main receive loop ──
        await _receive_loop(conn)

    except WebSocketDisconnect:
        logger.info(
            "WebSocket disconnected",
            extra={"session_id": session_id, "event": "ws_disconnect"},
        )
    except Exception as e:
        logger.error(
            f"WebSocket error: {e}",
            extra={"session_id": session_id, "event": "ws_error"},
            exc_info=True,
        )
    finally:
        # ── Cleanup ──
        await _cleanup(conn)


async def _receive_loop(conn: ConnectionState):
    """Receive and route incoming WebSocket messages."""
    while True:
        try:
            raw = await conn.ws.receive_json()
            msg = ClientMessage(**raw)
            conn.last_activity = time.time()

            match msg.type:
                case ClientMessageType.AUDIO_CHUNK:
                    await conn.orchestrator.handle_audio_chunk(msg.data, msg.seq)

                case ClientMessageType.TEXT_INPUT:
                    await conn.orchestrator.handle_text_input(msg.data, msg.seq)

                case ClientMessageType.VIDEO_FRAME:
                    await conn.orchestrator.handle_video_frame(
                        msg.data,
                        msg.width or 640,
                        msg.height or 480,
                        msg.seq,
                    )

                case ClientMessageType.INTERRUPT:
                    await conn.orchestrator.handle_interrupt()

                case ClientMessageType.CONTROL:
                    await conn.orchestrator.handle_control(msg.action, msg.value)

                case ClientMessageType.PING:
                    await _send_message(
                        conn, ServerMessage(type=ServerMessageType.PONG)
                    )

                case ClientMessageType.SESSION_INIT:
                    # Resume existing session
                    if msg.session_id:
                        await conn.orchestrator.restore_session(msg.session_id)

        except WebSocketDisconnect:
            raise
        except Exception as e:
            logger.warning(
                f"Error processing message: {e}",
                extra={"session_id": conn.session_id, "event": "msg_error"},
            )
            await conn.output_queue.put(
                ServerMessage(
                    type=ServerMessageType.ERROR,
                    error=str(e),
                )
            )


async def _output_sender(conn: ConnectionState):
    """
    Continuously drain the output queue and send to client.
    
    This runs as a separate task so that the receive loop and
    output sending are fully concurrent — true bidirectional streaming.
    """
    try:
        while conn.ws.client_state == WebSocketState.CONNECTED:
            try:
                msg = await asyncio.wait_for(
                    conn.output_queue.get(), timeout=30.0
                )
                await _send_message(conn, msg)
            except asyncio.TimeoutError:
                # Send keepalive ping
                await _send_message(
                    conn, ServerMessage(type=ServerMessageType.PONG)
                )
            except Exception as e:
                logger.warning(
                    f"Output sender error: {e}",
                    extra={"session_id": conn.session_id},
                )
                break
    except asyncio.CancelledError:
        pass


async def _send_message(conn: ConnectionState, msg: ServerMessage):
    """Send a message to the client with sequence number."""
    try:
        if conn.ws.client_state == WebSocketState.CONNECTED:
            msg.seq = conn.next_seq()
            await conn.ws.send_json(msg.model_dump(exclude_none=True))
    except Exception as e:
        logger.warning(
            f"Send failed: {e}",
            extra={"session_id": conn.session_id},
        )


async def _cleanup(conn: ConnectionState):
    """Clean up connection resources."""
    # Cancel sender task
    if conn._send_task and not conn._send_task.done():
        conn._send_task.cancel()
        try:
            await conn._send_task
        except asyncio.CancelledError:
            pass

    # Shutdown orchestrator
    if conn.orchestrator:
        await conn.orchestrator.shutdown()

    # Remove from active connections
    active_connections.pop(conn.session_id, None)

    logger.info(
        "Connection cleaned up",
        extra={"session_id": conn.session_id, "event": "ws_cleanup"},
    )
