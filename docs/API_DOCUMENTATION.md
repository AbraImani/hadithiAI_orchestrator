# HadithiAI Orchestrator — API Documentation

> **Version:** 0.1.0  
> **Base URL:** `https://hadithiai-orchestrator-292237971535.us-central1.run.app` (production) or `http://localhost:8080` (dev)  
> **Protocol:** REST (JSON) + WebSocket (JSON frames)

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Architecture Overview](#architecture-overview)
3. [REST API — Session Management](#rest-api--session-management)
4. [WebSocket — Real-Time Communication](#websocket--real-time-communication)
5. [Audio & Video Specifications](#audio--video-specifications)
6. [Agent System](#agent-system)
7. [Error Handling](#error-handling)
8. [Flutter Integration Guide](#flutter-integration-guide)
9. [Configuration](#configuration)

---

## Quick Start

HadithiAI has **two communication channels**:

| Channel | Endpoint | Purpose |
|---------|----------|---------|
| **REST** | `/api/v1/*` | Session management only (create, read, delete, history, preferences) |
| **WebSocket** | `/ws` | **ALL real-time communication** — audio, text, video, interrupts |

> ⚠️ **Important:** There is NO REST endpoint for audio or vision.  
> Audio streaming, text chat, and video frames ALL go through the WebSocket endpoint `/ws`.

### Minimal Flow (3 Steps)

```
Step 1: POST /api/v1/sessions → Get session_id
Step 2: Connect to ws://<host>/ws?session_id=<session_id>
Step 3: Send/receive audio_chunk, text_input, video_frame via WebSocket
```

---

## Architecture Overview

```
┌──────────────┐
│  Flutter App  │
└──────┬───────┘
       │
       ├── REST API (/api/v1/*)
       │     └── Session CRUD, preferences, history, agents list
       │         (NO audio, NO video, NO streaming)
       │
       └── WebSocket (/ws)
             └── Real-time bidirectional communication:
                   • audio_chunk  → Send/receive audio (PCM, base64)
                   • text_input   → Send text messages
                   • video_frame  → Send camera frames (base64 JPEG/PNG)
                   • interrupt    → Stop AI response
                   │
                   └── Server (Orchestrator)
                         ├── Gemini Live API (native audio generation)
                         └── Sub-agents:
                               • Story Agent (African stories)
                               • Riddle Agent (African riddles)
                               • Cultural Grounding (context/validation)
                               • Visual Agent (illustrations)
                               • Memory Context (session memory)
```

---

## REST API — Session Management

All REST endpoints use JSON. Prefix: `/api/v1`.

> These endpoints are for managing sessions only. They do NOT handle audio, video, or real-time streaming.

### Health Checks

#### `GET /health`
Basic liveness check.

```json
// Response 200
{ "status": "healthy", "service": "hadithiai-live" }
```

#### `GET /ready`
Readiness check with connection count.

```json
// Response 200
{ "status": "ready", "active_connections": 2 }
```

#### `GET /api/v1/health`
Detailed health with uptime and Gemini pool status.

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

### Sessions

#### `POST /api/v1/sessions` — Create Session

**Request:**
```json
{
  "language": "en",        // optional, default "en"
  "region": "east-africa", // optional
  "age_group": "adult"     // optional, default "adult"
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

> 💡 Use the returned `websocket_url` to open the WebSocket connection.

**cURL:**
```bash
curl -X POST https://<host>/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"language": "en", "age_group": "child"}'
```

---

#### `GET /api/v1/sessions/{session_id}` — Get Session

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

**Response 404:** `{ "detail": "Session not found" }`

---

#### `DELETE /api/v1/sessions/{session_id}` — End Session

**Response 200:**
```json
{ "status": "ended", "session_id": "a1b2c3d4e5f6" }
```

---

#### `GET /api/v1/sessions/{session_id}/history` — Get History

| Query Param | Type | Default | Description |
|-------------|------|---------|-------------|
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

#### `POST /api/v1/sessions/{session_id}/preferences` — Update Preferences

**Request:**
```json
{
  "language": "sw",
  "age_group": "child",
  "region": "west-africa"
}
```

**Response 200:**
```json
{
  "status": "updated",
  "session_id": "a1b2c3d4e5f6",
  "updates": { "language_pref": "sw", "region_pref": "west-africa" }
}
```

---

### Agent Discovery

#### `GET /api/v1/agents` — List Agents

**Response 200:**
```json
{
  "agents": [
    {
      "name": "story_agent",
      "description": "Generates culturally-rooted African stories",
      "capabilities": ["streaming", "visual_moments"]
    },
    {
      "name": "riddle_agent",
      "description": "Creates and manages African riddles"
    },
    {
      "name": "cultural_grounding",
      "description": "Provides cultural context and validation"
    },
    {
      "name": "visual_agent",
      "description": "Generates culturally appropriate illustrations"
    },
    {
      "name": "memory_context",
      "description": "Tracks session memory and context"
    }
  ],
  "total": 5
}
```

---

## WebSocket — Real-Time Communication

> **This is the main endpoint.** All audio, text, video, and interactive features go through here.

### Connection

```
ws://<host>/ws?session_id=<optional_session_id>
wss://<host>/ws?session_id=<optional_session_id>  (production with TLS)
```

If `session_id` is omitted, the server generates a new one.

### Connection Lifecycle

```
Client                              Server
  │                                    │
  │─── WebSocket Connect ────────────>│
  │<── session_created ───────────────│  (you now have session_id)
  │                                    │
  │─── audio_chunk (base64 PCM) ─────>│  🎤 streaming audio input
  │─── text_input ───────────────────>│  ⌨️  text message
  │─── video_frame (base64 JPEG) ────>│  📷 camera frame
  │                                    │
  │<── audio_chunk (base64 PCM) ──────│  🔊 AI audio response
  │<── text_chunk ────────────────────│  💬 AI text response
  │<── image_ready ───────────────────│  🖼️  generated illustration
  │<── agent_state ───────────────────│  ℹ️  status update
  │<── turn_end ──────────────────────│  ✅ response complete
  │                                    │
  │─── interrupt ────────────────────>│  🛑 stop current response
  │<── interrupted ───────────────────│  ✅ confirmed
  │                                    │
  │─── ping ─────────────────────────>│  💓 keepalive
  │<── pong ──────────────────────────│
```

---

### Client → Server Messages

Every message is a JSON object with a `type` field:

#### 1. `audio_chunk` — Stream Audio Input 🎤

Send microphone audio to the AI. The AI will process it and respond with audio.

```json
{
  "type": "audio_chunk",
  "data": "<base64-encoded PCM audio>",
  "seq": 1
}
```

- **Format:** 16-bit PCM, 16kHz, mono
- **Chunk size:** ~100ms = 3,200 bytes raw → base64-encoded
- **Stream continuously** while the user is speaking

---

#### 2. `text_input` — Send Text Message ⌨️

Send a text message instead of audio.

```json
{
  "type": "text_input",
  "data": "Tell me an Anansi trickster story",
  "seq": 2
}
```

---

#### 3. `video_frame` — Send Camera Frame 📷

Send a camera/image frame for visual context (e.g., show a book page to the AI).

```json
{
  "type": "video_frame",
  "data": "<base64-encoded JPEG or PNG>",
  "width": 640,
  "height": 480,
  "seq": 3
}
```

---

#### 4. `interrupt` — Stop Current Response 🛑

Immediately stop the AI's current response (like pressing a stop button).

```json
{
  "type": "interrupt",
  "seq": 4
}
```

---

#### 5. `control` — Change Settings

Change settings during a session.

```json
{
  "type": "control",
  "action": "set_language",
  "value": "sw",
  "seq": 5
}
```

---

#### 6. `ping` — Keepalive

```json
{ "type": "ping", "seq": 6 }
```

---

#### 7. `session_init` — Resume Session

Connect to an existing session.

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
Sent immediately after WebSocket connection.

```json
{
  "type": "session_created",
  "session_id": "a1b2c3d4e5f6",
  "seq": 1,
  "timestamp": 1709654400.123
}
```

#### `audio_chunk` — AI Audio Response 🔊
Streaming AI-generated audio. Play these chunks as they arrive.

```json
{
  "type": "audio_chunk",
  "data": "<base64-encoded PCM audio>",
  "seq": 2,
  "timestamp": 1709654400.456
}
```

- **Format:** 16-bit PCM, **24kHz**, mono
- Multiple chunks arrive per response — play them sequentially

#### `text_chunk` — AI Text Response 💬

```json
{
  "type": "text_chunk",
  "data": "Once upon a time, in the land of the Ashanti...",
  "agent": "orchestrator",
  "seq": 3,
  "timestamp": 1709654400.789
}
```

#### `image_ready` — Generated Illustration 🖼️

```json
{
  "type": "image_ready",
  "url": "https://storage.googleapis.com/hadithiai-media/img_abc123.png",
  "agent": "visual",
  "seq": 10,
  "timestamp": 1709654410.0
}
```

#### `agent_state` — Agent Status ℹ️

```json
{
  "type": "agent_state",
  "agent": "story",
  "state": "generating",
  "seq": 4,
  "timestamp": 1709654401.0
}
```

#### `turn_end` — Response Complete ✅
The AI finished responding. The client can enable the microphone / send next input.

```json
{
  "type": "turn_end",
  "seq": 100,
  "timestamp": 1709654430.0
}
```

#### `interrupted` — Interrupt Confirmed

```json
{
  "type": "interrupted",
  "seq": 50,
  "timestamp": 1709654415.0
}
```

#### `error` — Error

```json
{
  "type": "error",
  "error": "AI processing error",
  "seq": 5,
  "timestamp": 1709654402.0
}
```

#### `pong` — Keepalive Response

```json
{ "type": "pong", "seq": 7, "timestamp": 1709654403.0 }
```

---

## Audio & Video Specifications

### Audio Format

| Direction | Format | Sample Rate | Channels | Bit Depth | Encoding |
|-----------|--------|-------------|----------|-----------|----------|
| **Client → Server** | PCM | 16,000 Hz | Mono | 16-bit | base64 |
| **Server → Client** | PCM | 24,000 Hz | Mono | 16-bit | base64 |

- **Chunk duration:** ~100ms per audio_chunk
- **Raw chunk size (input):** 3,200 bytes → base64 ≈ 4,267 chars
- Stream continuously while user speaks

### Video Format

| Property | Value |
|----------|-------|
| Format | JPEG or PNG |
| Encoding | base64 |
| Recommended size | 640×480 |
| Purpose | Visual context for the AI (e.g., show a book, drawing, etc.) |

---

## Agent System

HadithiAI uses 5 specialized sub-agents, automatically triggered by the AI via function calling:

| Agent | Trigger (Automatic) | What It Does |
|-------|---------------------|--------------|
| **Story Agent** | User asks for a story | Generates African oral tradition stories (Ashanti, Yoruba, Zulu, Swahili, Maasai) |
| **Riddle Agent** | User wants a riddle | Creates African riddles with hints and cultural explanations |
| **Cultural Grounding** | Internal validation | Ensures cultural accuracy and provides context |
| **Visual Agent** | Story has a visual moment | Generates culturally appropriate illustrations |
| **Memory Context** | Internal | Tracks conversation memory and user preferences |

### How It Works (Inside)

```
1. User speaks: "Tell me an Anansi story" (via audio_chunk or text_input)
2. Gemini Live API understands → calls function: tell_story(culture="Ashanti", theme="trickster")
3. Orchestrator dispatches to Story Agent
4. Story Agent generates the story using gemini-2.0-flash (text model)
5. Story text is sent back to Gemini Live API
6. Gemini synthesizes the story into natural audio
7. Audio chunks stream back to client via WebSocket
```

> **You don't call agents directly.** Just talk to the AI naturally, and it automatically routes to the right agent.

---

## Error Handling

### REST Errors

| HTTP Status | Meaning |
|-------------|---------|
| 200 | Success |
| 404 | Resource not found (invalid session_id) |
| 422 | Validation error (invalid request body) |
| 500 | Internal server error |

### WebSocket Errors

Errors are sent as `error` type messages. Fatal errors close the connection.

| Error | Cause | Resolution |
|-------|-------|------------|
| `Server initialization failed: timed out` | Gemini API connection timeout | Retry WebSocket connection |
| `1008 Operation not supported` | Gemini session expired | Reconnect with new session |
| `Session not found` | Invalid session_id | Create a new session via REST |

---

## Flutter Integration Guide

### Complete Flow

```dart
import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';

const baseUrl = 'https://<cloud-run-url>';

// ─── Step 1: Create Session (REST) ───
final response = await http.post(
  Uri.parse('$baseUrl/api/v1/sessions'),
  headers: {'Content-Type': 'application/json'},
  body: jsonEncode({
    'language': 'en',
    'age_group': 'child',
  }),
);
final session = jsonDecode(response.body);
final sessionId = session['session_id'];

// ─── Step 2: Connect WebSocket ───
final wsUrl = 'wss://<cloud-run-url>/ws?session_id=$sessionId';
final channel = WebSocketChannel.connect(Uri.parse(wsUrl));

// ─── Step 3: Listen for responses ───
channel.stream.listen((message) {
  final msg = jsonDecode(message);
  
  switch (msg['type']) {
    case 'session_created':
      print('Connected! Session: ${msg['session_id']}');
      break;
      
    case 'audio_chunk':
      // Decode base64 → PCM bytes → play audio (24kHz, 16-bit, mono)
      final audioBytes = base64Decode(msg['data']);
      audioPlayer.playPcm(audioBytes, sampleRate: 24000);
      break;
      
    case 'text_chunk':
      // Show subtitle/caption
      showCaption(msg['data']);
      break;
      
    case 'image_ready':
      // Display generated illustration
      showImage(msg['url']);
      break;
      
    case 'turn_end':
      // AI finished responding → enable microphone
      enableMicrophone();
      break;
      
    case 'interrupted':
      // Interrupt confirmed
      break;
      
    case 'error':
      print('Error: ${msg['error']}');
      break;
  }
});

// ─── Step 4: Send audio from microphone ───
int seq = 1;

void onMicrophoneData(List<int> pcmBytes) {
  channel.sink.add(jsonEncode({
    'type': 'audio_chunk',
    'data': base64Encode(pcmBytes),  // 16kHz, 16-bit, mono PCM
    'seq': seq++,
  }));
}

// ─── Step 5: Send text instead of audio ───
void sendText(String text) {
  channel.sink.add(jsonEncode({
    'type': 'text_input',
    'data': text,
    'seq': seq++,
  }));
}

// ─── Step 6: Interrupt AI ───
void interrupt() {
  channel.sink.add(jsonEncode({
    'type': 'interrupt',
    'seq': seq++,
  }));
}

// ─── Step 7: Send camera frame (optional) ───
void sendCameraFrame(List<int> jpegBytes) {
  channel.sink.add(jsonEncode({
    'type': 'video_frame',
    'data': base64Encode(jpegBytes),
    'width': 640,
    'height': 480,
    'seq': seq++,
  }));
}

// ─── Cleanup ───
await channel.sink.close();
await http.delete(Uri.parse('$baseUrl/api/v1/sessions/$sessionId'));
```

### Summary for Flutter Developer

| What You Want | How to Do It |
|---------------|--------------|
| Create a session | `POST /api/v1/sessions` |
| Stream audio to AI | WebSocket → send `audio_chunk` messages |
| Receive AI audio | WebSocket → listen for `audio_chunk` messages |
| Send text to AI | WebSocket → send `text_input` message |
| Receive AI text | WebSocket → listen for `text_chunk` messages |
| Send camera/image | WebSocket → send `video_frame` message |
| Receive illustrations | WebSocket → listen for `image_ready` messages |
| Stop AI response | WebSocket → send `interrupt` message |
| Know when AI is done | WebSocket → listen for `turn_end` message |
| Get conversation history | `GET /api/v1/sessions/{id}/history` |
| Delete session | `DELETE /api/v1/sessions/{id}` |

---

## Configuration

Environment variables (set in Cloud Run or `.env` for local dev):

| Variable | Default | Description |
|----------|---------|-------------|
| `HADITHI_GEMINI_API_KEY` | — | **Required.** Google Gemini API key |
| `HADITHI_PROJECT_ID` | `hadithiai-live` | GCP project ID |
| `HADITHI_REGION` | `us-central1` | GCP region |
| `HADITHI_GEMINI_MODEL` | `gemini-2.5-flash-native-audio-latest` | Gemini Live API model |
| `HADITHI_GEMINI_TEXT_MODEL` | `gemini-2.0-flash` | Text generation model |
| `HADITHI_GEMINI_VOICE` | `Zephyr` | Voice for audio synthesis |
| `HADITHI_GEMINI_POOL_SIZE` | `3` | Number of warm Gemini sessions |
| `HADITHI_DEBUG` | `false` | Enable debug logging |
| `HADITHI_LOG_LEVEL` | `INFO` | Log level |
| `HADITHI_MAX_CONCURRENT_SESSIONS` | `200` | Max simultaneous WebSocket connections |
