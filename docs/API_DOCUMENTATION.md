# HadithiAI Orchestrator — API Documentation

> **Version:** 2.0.0  
> **Base URL:** `http://localhost:8080` (dev) or `https://<cloud-run-url>` (prod)  
> **Protocol:** REST (JSON) + WebSocket (JSON frames)

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [REST API Endpoints](#rest-api-endpoints)
4. [WebSocket Protocol](#websocket-protocol)
5. [Agent Cards](#agent-cards)
6. [Error Handling](#error-handling)
7. [Flutter Integration Guide](#flutter-integration-guide)

---

## Overview

HadithiAI is an African Immersive Oral AI Agent combining:

- **Gemini Live API** for real-time bidirectional audio streaming
- **REST API** for session management and non-streaming operations
- **WebSocket** for real-time audio/text/video communication
- **Sub-agents** for story generation, riddles, cultural context, and image generation

### Architecture

```
Flutter App
   ├── REST API (/api/v1/*) → Session CRUD, preferences, history
   └── WebSocket (/ws)      → Real-time audio/text streaming
         └── Orchestrator → Gemini Live API (native audio)
                          → Sub-agents (story, riddle, cultural, visual)
```

---

## Authentication

| Method | Usage |
|--------|-------|
| **API Key** | Set `GEMINI_API_KEY` in `.env` (development) |
| **ADC** | Google Application Default Credentials (Cloud Run) |

The server authenticates with Google's Gemini API internally. Client-to-server authentication is not currently enforced (add your own auth middleware for production).

---

## REST API Endpoints

All REST endpoints are prefixed with `/api/v1`.

### Health Endpoints

#### `GET /health`
Basic liveness probe.

```json
// Response 200
{
  "status": "healthy",
  "service": "hadithiai-live"
}
```

#### `GET /ready`
Readiness probe with active connection count.

```json
// Response 200
{
  "status": "ready",
  "active_connections": 2
}
```

#### `GET /api/v1/health`
Detailed health check with uptime and Gemini pool status.

```json
// Response 200
{
  "status": "healthy",
  "service": "hadithiai-live",
  "version": "2.0.0",
  "uptime_seconds": 3600.5,
  "active_sessions": 2,
  "gemini_pool_ready": true
}
```

---

### Session Management

#### `POST /api/v1/sessions`
Create a new conversation session.

**Request:**
```json
{
  "language": "en",       // optional, default "en"
  "region": "east-africa", // optional
  "age_group": "adult"    // optional, default "adult"
}
```

**Response 200:**
```json
{
  "session_id": "a1b2c3d4e5f6",
  "websocket_url": "ws://localhost:8080/ws?session_id=a1b2c3d4e5f6",
  "created_at": 1709654400.123
}
```

**cURL:**
```bash
curl -X POST http://localhost:8080/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"language": "en", "age_group": "child"}'
```

**PowerShell:**
```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/v1/sessions `
  -ContentType "application/json" `
  -Body '{"language":"en","age_group":"child"}'
```

---

#### `GET /api/v1/sessions/{session_id}`
Get session metadata.

**Response 200:**
```json
{
  "session_id": "a1b2c3d4e5f6",
  "created_at": 1709654400.123,
  "last_active": 1709654500.456,
  "turn_count": 5,
  "language": "en",
  "region": "east-africa",
  "age_group": "adult"
}
```

**Response 404:**
```json
{
  "detail": "Session not found"
}
```

---

#### `DELETE /api/v1/sessions/{session_id}`
End and clean up a session.

**Response 200:**
```json
{
  "status": "ended",
  "session_id": "a1b2c3d4e5f6"
}
```

---

#### `GET /api/v1/sessions/{session_id}/history`
Get conversation history.

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | 50 | Max turns to return |

**Response 200:**
```json
{
  "session_id": "a1b2c3d4e5f6",
  "turns": [
    {
      "turn_id": "turn_abc123",
      "role": "user",
      "content": "Tell me an Anansi story",
      "timestamp": 1709654400.0
    },
    {
      "turn_id": "turn_def456",
      "role": "agent",
      "content": "Ah, gather close...",
      "agent": "story",
      "timestamp": 1709654401.0
    }
  ],
  "total": 2
}
```

---

#### `POST /api/v1/sessions/{session_id}/preferences`
Update user preferences.

**Request:**
```json
{
  "language": "sw",          // optional
  "age_group": "child",     // optional
  "region": "west-africa"   // optional
}
```

**Response 200:**
```json
{
  "status": "updated",
  "session_id": "a1b2c3d4e5f6",
  "updates": {
    "language_pref": "sw",
    "region_pref": "west-africa"
  }
}
```

---

### Agent Discovery

#### `GET /api/v1/agents`
List available agent capabilities (Agent Cards).

**Response 200:**
```json
{
  "agents": [
    {
      "name": "story_agent",
      "description": "Generates culturally-rooted African stories",
      "input_schema": "StoryRequest",
      "output_schema": "StoryChunk",
      "supported_cultures": ["Ashanti", "Yoruba", "Zulu", "Swahili", "Maasai"],
      "capabilities": ["streaming", "visual_moments"]
    },
    {
      "name": "riddle_agent",
      "description": "Creates and manages African riddles",
      "input_schema": "RiddleRequest",
      "output_schema": "RiddlePayload"
    },
    {
      "name": "cultural_grounding",
      "description": "Provides cultural context and validation",
      "capabilities": ["fact_checking", "cultural_validation"]
    },
    {
      "name": "visual_agent",
      "description": "Generates culturally appropriate illustrations",
      "input_schema": "ImageRequest",
      "output_schema": "ImageResult"
    },
    {
      "name": "memory_context",
      "description": "Tracks session memory and context",
      "capabilities": ["context_summary", "preference_tracking"]
    }
  ],
  "total": 5
}
```

---

## WebSocket Protocol

### Connection

```
ws://localhost:8080/ws?session_id=<optional_session_id>
```

If `session_id` is omitted, a new one is generated server-side.

### Connection Lifecycle

```
Client                          Server
  |                               |
  |--- WebSocket Connect -------->|
  |<-- session_created -----------|
  |                               |
  |--- audio_chunk (base64) ----->|  ← streaming audio input
  |--- text_input --------------->|  ← text message
  |--- video_frame (base64) ----->|  ← optional video
  |                               |
  |<-- audio_chunk (base64) ------|  ← streaming audio response
  |<-- text_chunk ----------------|  ← text/thought response
  |<-- image_ready ---------------|  ← generated illustration URL
  |<-- agent_state ---------------|  ← agent status update
  |<-- turn_end ------------------|  ← end of response turn
  |                               |
  |--- interrupt ----------------->|  ← stop current response
  |<-- interrupted  ---------------|  ← (optional) confirm
  |                               |
  |--- ping ---------------------->|
  |<-- pong ----------------------|
```

### Client → Server Messages

#### `audio_chunk`
Stream audio data to the AI.

```json
{
  "type": "audio_chunk",
  "data": "<base64-encoded PCM audio>",
  "seq": 1
}
```

Audio format: **16-bit PCM, 16kHz, mono**. Chunk size: ~100ms (3200 bytes raw → base64 encoded).

#### `text_input`
Send a text message.

```json
{
  "type": "text_input",
  "data": "Tell me an Anansi trickster story",
  "seq": 2
}
```

#### `video_frame`
Send a video frame for visual context.

```json
{
  "type": "video_frame",
  "data": "<base64-encoded JPEG/PNG>",
  "width": 640,
  "height": 480,
  "seq": 3
}
```

#### `interrupt`
Stop the current response immediately.

```json
{
  "type": "interrupt",
  "seq": 4
}
```

#### `control`
Change settings mid-session.

```json
{
  "type": "control",
  "action": "set_language",
  "value": "sw",
  "seq": 5
}
```

#### `ping`
Keepalive ping.

```json
{
  "type": "ping",
  "seq": 6
}
```

#### `session_init`
Resume an existing session.

```json
{
  "type": "session_init",
  "session_id": "a1b2c3d4e5f6",
  "seq": 0
}
```

---

### Server → Client Messages

#### `session_created`
Sent immediately after connection.

```json
{
  "type": "session_created",
  "session_id": "a1b2c3d4e5f6",
  "seq": 1,
  "timestamp": 1709654400.123
}
```

#### `audio_chunk`
Streaming audio response (24kHz PCM, base64-encoded).

```json
{
  "type": "audio_chunk",
  "data": "<base64-encoded PCM audio>",
  "seq": 2,
  "timestamp": 1709654400.456
}
```

Audio format: **16-bit PCM, 24kHz, mono**.

#### `text_chunk`
Text response (Gemini's thoughts or agent text).

```json
{
  "type": "text_chunk",
  "data": "Once upon a time, in the land of the Ashanti...",
  "agent": "orchestrator",
  "seq": 3,
  "timestamp": 1709654400.789
}
```

#### `image_ready`
A generated illustration is ready.

```json
{
  "type": "image_ready",
  "url": "https://storage.googleapis.com/hadithiai-media/img_abc123.png",
  "agent": "visual",
  "seq": 10,
  "timestamp": 1709654410.0
}
```

#### `agent_state`
Agent processing status update.

```json
{
  "type": "agent_state",
  "agent": "story",
  "state": "generating",
  "seq": 4,
  "timestamp": 1709654401.0
}
```

#### `turn_end`
End of the current response turn. Client can now send the next input.

```json
{
  "type": "turn_end",
  "seq": 100,
  "timestamp": 1709654430.0
}
```

#### `interrupted`
Confirms the interrupt was processed.

```json
{
  "type": "interrupted",
  "seq": 50,
  "timestamp": 1709654415.0
}
```

#### `error`
An error occurred.

```json
{
  "type": "error",
  "error": "AI processing error",
  "seq": 5,
  "timestamp": 1709654402.0
}
```

#### `pong`
Keepalive response.

```json
{
  "type": "pong",
  "seq": 7,
  "timestamp": 1709654403.0
}
```

---

## Agent Cards

HadithiAI uses 5 specialized sub-agents orchestrated by Gemini Live API function calling:

| Agent | Trigger Function | Description |
|-------|-----------------|-------------|
| **Story Agent** | `tell_story(culture, theme, complexity)` | African oral tradition stories |
| **Riddle Agent** | `pose_riddle(culture, difficulty)` | African riddles with hints |
| **Cultural Grounding** | `get_cultural_context(topic, culture)` | Cultural context and validation |
| **Visual Agent** | `generate_scene_image(scene, culture)` | Culturally appropriate illustrations |
| **Memory Context** | (internal) | Session memory and context tracking |

### Function Call Flow

```
1. User says: "Tell me an Anansi story"
2. Gemini Live API detects intent → function_call: tell_story(...)
3. Orchestrator dispatches to Story Agent
4. Story Agent uses gemini-2.0-flash (text) to generate story
5. Result sent back to Gemini as tool_response
6. Gemini synthesizes story into native audio
7. Audio chunks streamed to client
```

---

## Error Handling

| HTTP Status | Meaning |
|-------------|---------|
| 200 | Success |
| 404 | Resource not found (invalid session_id) |
| 422 | Validation error (invalid request body) |
| 500 | Internal server error |

WebSocket errors are sent as `error` type messages. Fatal errors close the connection.

### Common Error Scenarios

| Error | Cause | Resolution |
|-------|-------|------------|
| `Server initialization failed: timed out` | Gemini API connection timeout | Retry connection |
| `1008 Operation not supported` | Gemini session closed unexpectedly | Reconnect with new session |
| `Session not found` | Invalid or expired session_id | Create a new session |

---

## Flutter Integration Guide

### Typical Flow

```dart
// 1. Create session via REST
final session = await http.post('/api/v1/sessions', body: {
  'language': 'en',
  'age_group': 'child',
});
final sessionId = session['session_id'];
final wsUrl = session['websocket_url'];

// 2. Connect WebSocket
final ws = await WebSocket.connect(wsUrl);

// 3. Wait for session_created
final created = await ws.first; // {"type": "session_created", ...}

// 4. Stream audio
ws.add(jsonEncode({
  'type': 'audio_chunk',
  'data': base64Encode(pcmAudioBytes),
  'seq': seqCounter++,
}));

// 5. Receive responses
ws.listen((msg) {
  final data = jsonDecode(msg);
  switch (data['type']) {
    case 'audio_chunk':
      playAudio(base64Decode(data['data']));
      break;
    case 'text_chunk':
      showCaption(data['data']);
      break;
    case 'turn_end':
      enableMicrophone();
      break;
    case 'image_ready':
      showImage(data['url']);
      break;
  }
});

// 6. Interrupt
ws.add(jsonEncode({'type': 'interrupt', 'seq': seqCounter++}));
```

### Audio Configuration

| Parameter | Input (Client → Server) | Output (Server → Client) |
|-----------|------------------------|--------------------------|
| Format | PCM 16-bit | PCM 16-bit |
| Sample Rate | 16,000 Hz | 24,000 Hz |
| Channels | Mono | Mono |
| Chunk Duration | ~100ms | Variable |

---

## Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | — | Google Gemini API key |
| `HADITHI_PROJECT_ID` | `hadithiai-live` | GCP project ID |
| `HADITHI_REGION` | `us-central1` | GCP region |
| `HADITHI_GEMINI_MODEL` | `gemini-2.5-flash-native-audio-latest` | Live API model |
| `HADITHI_GEMINI_TEXT_MODEL` | `gemini-2.0-flash` | Text generation model |
| `HADITHI_GEMINI_VOICE` | `Zephyr` | Voice for speech synthesis |
| `HADITHI_GEMINI_POOL_SIZE` | `3` | Number of warm sessions |
| `HADITHI_DEBUG` | `false` | Enable debug logging |
| `HADITHI_LOG_LEVEL` | `INFO` | Log level |
| `HADITHI_MAX_CONCURRENT_SESSIONS` | `200` | Max simultaneous connections |
