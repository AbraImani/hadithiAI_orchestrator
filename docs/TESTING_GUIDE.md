# HadithiAI Orchestrator — Testing Guide

> Complete guide to testing the HadithiAI backend, from startup verification to live agent testing.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start Verification](#quick-start-verification)
3. [Unit Tests](#unit-tests)
4. [REST API Testing](#rest-api-testing)
5. [WebSocket Pipeline Testing](#websocket-pipeline-testing)
6. [Agent Testing](#agent-testing)
7. [Interrupt & Multi-turn Testing](#interrupt--multi-turn-testing)
8. [Test Results Reference](#test-results-reference)
9. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Environment Setup

1. **Python 3.10+** installed
2. **Dependencies** installed:
   ```bash
   pip install -r requirements.txt
   ```
3. **`.env` file** at project root with your Gemini API key:
   ```env
   GEMINI_API_KEY=your_api_key_here
   ```
4. **websockets** package for live tests:
   ```bash
   pip install websockets
   ```

### Required Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | Yes | Google Gemini API key |
| `HADITHI_GEMINI_MODEL` | No | Default: `gemini-2.5-flash-native-audio-latest` |
| `HADITHI_GEMINI_VOICE` | No | Default: `Zephyr` |
| `HADITHI_DEBUG` | No | Set `true` for verbose logging |

---

## Quick Start Verification

### 1. Start the Server

```bash
cd src
python main.py
```

Expected output:
```
INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
INFO:     HadithiAI Live starting up...
INFO:     Gemini client pool warmed up with 3 sessions
INFO:     HadithiAI Live ready to serve
INFO:     Application startup complete.
```

### 2. Test Health Endpoints

**PowerShell:**
```powershell
# Liveness
Invoke-RestMethod -Uri http://localhost:8080/health
# Expected: status=healthy, service=hadithiai-live

# Readiness
Invoke-RestMethod -Uri http://localhost:8080/ready
# Expected: status=ready, active_connections=0

# Detailed health
Invoke-RestMethod -Uri http://localhost:8080/api/v1/health
# Expected: gemini_pool_ready=True, uptime_seconds > 0
```

**cURL:**
```bash
curl http://localhost:8080/health
curl http://localhost:8080/ready
curl http://localhost:8080/api/v1/health
```

### 3. Test Session Creation

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/v1/sessions `
  -ContentType "application/json" -Body '{}'
# Expected: session_id, websocket_url
```

### 4. Test Agent Discovery

```powershell
Invoke-RestMethod -Uri http://localhost:8080/api/v1/agents
# Expected: 5 agents (story_agent, riddle_agent, cultural_grounding, visual_agent, memory_context)
```

If all 4 checks pass, the backend is operational.

---

## Unit Tests

### Running All Tests

```bash
cd d:\FAITH\Faith\perso\hadithiAI_orchestrator
$env:PYTHONPATH="src"
python -m pytest tests/test_orchestrator.py tests/test_schemas.py -v
```

### Expected Result

```
63 passed in ~2s
```

### Test Coverage

| Test File | Tests | What It Covers |
|-----------|-------|---------------|
| `test_orchestrator.py` | ~25 | Orchestrator state machine, initialization, message handling, interrupt logic |
| `test_schemas.py` | ~38 | A2A schemas (StoryRequest, RiddleRequest, ImageRequest), validation, agent cards |

### Running a Specific Test

```bash
$env:PYTHONPATH="src"
python -m pytest tests/test_orchestrator.py::TestOrchestratorInit -v
```

---

## REST API Testing

### Session CRUD

```powershell
# Create session
$session = Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/v1/sessions `
  -ContentType "application/json" -Body '{"language":"en","age_group":"child"}'
$sid = $session.session_id
Write-Host "Session: $sid"

# Get session info
Invoke-RestMethod -Uri "http://localhost:8080/api/v1/sessions/$sid"

# Update preferences
Invoke-RestMethod -Method Post -Uri "http://localhost:8080/api/v1/sessions/$sid/preferences" `
  -ContentType "application/json" -Body '{"language":"sw","region":"east-africa"}'

# Get history
Invoke-RestMethod -Uri "http://localhost:8080/api/v1/sessions/$sid/history?limit=10"

# Delete session
Invoke-RestMethod -Method Delete -Uri "http://localhost:8080/api/v1/sessions/$sid"
```

### Expected Error Cases

```powershell
# 404 for non-existent session
Invoke-RestMethod -Uri http://localhost:8080/api/v1/sessions/nonexistent
# Expected: 404 "Session not found"
```

---

## WebSocket Pipeline Testing

### Automated Test

```bash
$env:PYTHONIOENCODING="utf-8"
python tests/test_ws_live.py
```

This test:
1. Connects to `ws://localhost:8080/ws`
2. Sends a greeting text
3. Collects audio chunks, text chunks, and waits for `turn_end`
4. Verifies ping/pong keepalive
5. Reports message counts

### Expected Output

```
Session: <session_id>
Sending: "Hello, tell me about African storytelling traditions"
...
Received 900+ messages (800+ audio, 1+ text, turn_end)
PIPELINE: WORKING
```

### Manual WebSocket Test (Python)

```python
import asyncio
import json
import websockets

async def test():
    async with websockets.connect("ws://localhost:8080/ws") as ws:
        # Wait for session_created
        msg = json.loads(await ws.recv())
        print(f"Session: {msg['session_id']}")
        
        # Send text
        await ws.send(json.dumps({
            "type": "text_input",
            "data": "Hello, who are you?",
            "seq": 1
        }))
        
        # Collect responses
        audio_count = 0
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            evt = json.loads(raw)
            if evt["type"] == "audio_chunk":
                audio_count += 1
            elif evt["type"] == "text_chunk":
                print(f"Text: {evt['data'][:100]}")
            elif evt["type"] == "turn_end":
                break
        
        print(f"Audio chunks: {audio_count}")

asyncio.run(test())
```

---

## Agent Testing

### Automated Agent Test Suite

```bash
$env:PYTHONIOENCODING="utf-8"
python tests/test_agents_live.py
```

This tests all 5 agent scenarios:

| Test | Input | Expected Behavior |
|------|-------|-------------------|
| **Story Agent** | "Tell me an Ashanti Anansi trickster story" | Gemini calls `tell_story()`, returns audio + text |
| **Riddle Agent** | "Give me a Swahili riddle" | Gemini calls `pose_riddle()`, returns riddle audio |
| **Cultural Context** | "What is the significance of Anansi?" | Gemini calls `get_cultural_context()`, returns context |
| **Interrupt** | Long story request → interrupt after 3s | Audio stops, no messages after interrupt |
| **Multi-turn** | Follow-up question after other tests | Continues conversation with context |

### Expected Results

```
AGENT TEST SUMMARY
  story           PASS   msgs=1200+  audio=1200+  text=1+
  riddle          PASS   msgs= 800+  audio= 800+  text=1+
  cultural        PASS   msgs=1100+  audio=1100+  text=1+
  interrupt       PASS   before=300+  after=0
  followup        PASS   msgs= 500+  audio= 500+  text=1+
```

### Testing Individual Agents

You can test specific agents by sending targeted prompts via WebSocket:

**Story Agent triggers:** "Tell me a story", "Once upon a time", mentions of specific cultures + "story"
**Riddle Agent triggers:** "Give me a riddle", "Kitendawili" (Swahili for riddle)
**Cultural Context triggers:** "What is the significance of...", "Tell me about... tradition"

---

## Interrupt & Multi-turn Testing

### Focused Interrupt Test

```bash
$env:PYTHONIOENCODING="utf-8"
python tests/test_interrupt_followup.py
```

This test verifies:
1. Sending a long story request
2. Interrupting after a few seconds
3. Sending a follow-up question
4. Verifying the follow-up gets a response

### Expected Output

```
--- Step 1: Request long story ---
  Got 500+ audio chunks before interrupt

--- Step 2: Interrupt ---
  Drained 0 messages after interrupt

--- Step 3: Follow-up question ---
  Result: audio=3000+, text=2+, turn_end=True

  >>> FOLLOW-UP AFTER INTERRUPT: PASS <<<
```

### How Interrupt Works Internally

1. Client sends `{"type": "interrupt"}`
2. Server cancels active sub-agent tasks
3. If a function call was in progress, sends dummy `tool_response` to Gemini
4. Sets `_interrupted` flag to suppress stale events from old turn
5. On next `text_input`, flag is cleared and new response flows normally

---

## Test Results Reference

### Verified Test Results (Live)

| Test | Status | Details |
|------|--------|---------|
| Server startup | PASS | Starts in ~3s, binds port 8080 |
| `GET /health` | PASS | Returns `{"status": "healthy"}` |
| `GET /ready` | PASS | Returns `{"status": "ready"}` |
| `GET /api/v1/health` | PASS | `gemini_pool_ready=true` |
| `GET /api/v1/agents` | PASS | 5 agent cards returned |
| `POST /api/v1/sessions` | PASS | Returns session_id + WebSocket URL |
| `GET /api/v1/sessions/{bad_id}` | PASS | 404 as expected |
| WebSocket connect | PASS | Receives `session_created` |
| WebSocket text → audio | PASS | 900+ audio chunks streamed |
| Story Agent | PASS | Function call triggered, audio + text |
| Riddle Agent | PASS | Function call triggered, audio + text |
| Cultural Context | PASS | Function call triggered, audio + text |
| Interrupt | PASS | Audio stops immediately |
| Follow-up after interrupt | PASS | Response received after flag cleared |
| Unit tests | PASS | 63/63 pass |

---

## Troubleshooting

### Server Won't Start

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError` | Wrong directory | Run from `src/`: `cd src && python main.py` |
| `Address already in use` | Port 8080 occupied | Kill process: `Get-NetTCPConnection -LocalPort 8080 \| ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }` |
| `GEMINI_API_KEY not set` | Missing .env | Create `.env` at project root with `GEMINI_API_KEY=...` |

### Gemini API Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `timed out during opening handshake` | API connection timeout | Retry — network issue or rate limit |
| `1008 Operation not supported` | Session closed by Gemini | Reconnect with new session |
| `API key not valid` | Invalid or expired key | Check API key in `.env` |

### Test Failures

| Issue | Fix |
|-------|-----|
| `ModuleNotFoundError: No module named 'core'` | Set `$env:PYTHONPATH="src"` before running pytest |
| `UnicodeEncodeError` on Windows | Set `$env:PYTHONIOENCODING="utf-8"` |
| WebSocket test hangs | Check server is running on port 8080 |
| Agent test timeout | Gemini may be slow — increase timeout or retry |

### Windows-Specific

- Always use `$env:PYTHONIOENCODING="utf-8"` for live tests (Unicode output)
- Use `$env:PYTHONPATH="src"` for pytest from project root
- Use PowerShell `Invoke-RestMethod` (aliased as `curl` in PowerShell does NOT work like unix curl)
