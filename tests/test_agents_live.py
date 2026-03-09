"""
Agent testing via WebSocket function-call flow.
Tests: Story, Riddle, Cultural, Interrupt, Multi-turn.
Run: python tests/test_agents_live.py
"""
import asyncio
import json
import sys
import time

try:
    import websockets
except ImportError:
    print("pip install websockets")
    sys.exit(1)


async def send_and_collect(ws, text, label="", timeout=60):
    """Send text input and collect all response messages until turn_end or timeout."""
    await ws.send(json.dumps({"type": "text_input", "data": text, "seq": 1}))
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"SENT: {text}")
    print(f"{'='*60}")

    responses = []
    audio_count = 0
    text_parts = []
    t0 = time.time()

    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            evt = json.loads(raw)
            responses.append(evt)
            t = evt.get("type", "?")

            if t == "audio_chunk":
                audio_count += 1
            elif t == "text_chunk":
                txt = evt.get("data", "")
                text_parts.append(txt)
                print(f"  [text] {txt[:200]}")
            elif t == "turn_end":
                break
            elif t == "error":
                print(f"  [ERROR] {evt.get('error','?')}")
                break
            elif t == "image_ready":
                print(f"  [image] url={evt.get('url','')[:80]}")
            elif t == "agent_state":
                print(f"  [agent_state] {evt.get('agent')}={evt.get('state')}")
            elif t == "pong":
                pass
            else:
                print(f"  [{t}]")
    except asyncio.TimeoutError:
        print(f"  TIMEOUT after {len(responses)} messages")

    elapsed = time.time() - t0
    full_text = " ".join(text_parts)
    result = {
        "label": label,
        "total_msgs": len(responses),
        "audio_chunks": audio_count,
        "text_chunks": len(text_parts),
        "full_text": full_text,
        "elapsed_s": elapsed,
        "has_response": audio_count > 0 or len(text_parts) > 0,
    }
    print(f"  → messages={result['total_msgs']}, audio={audio_count}, text={len(text_parts)}, {elapsed:.1f}s")
    return result


async def test_interrupt(ws, label="Interrupt"):
    """Send text, then interrupt after 2 seconds."""
    await ws.send(json.dumps({
        "type": "text_input",
        "data": "Tell me a very long Zulu creation myth with lots of detail.",
        "seq": 10,
    }))
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"SENT: long story request")
    print(f"{'='*60}")

    # Collect for 3 seconds
    responses = []
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=3)
            evt = json.loads(raw)
            responses.append(evt)
    except asyncio.TimeoutError:
        pass

    before = len(responses)
    print(f"  Received {before} messages before interrupt")

    # Send interrupt
    await ws.send(json.dumps({"type": "interrupt", "seq": 11}))
    print(f"  SENT: interrupt")

    # Collect for 5 more seconds to see what happens
    after_msgs = []
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            evt = json.loads(raw)
            after_msgs.append(evt)
            t = evt.get("type", "?")
            if t == "interrupted":
                print(f"  [interrupted] ✓")
            elif t == "turn_end":
                print(f"  [turn_end] after interrupt ✓")
                break
    except asyncio.TimeoutError:
        pass

    print(f"  After interrupt: {len(after_msgs)} messages")
    return {"before": before, "after": len(after_msgs), "has_interrupted": any(m.get("type") == "interrupted" for m in after_msgs)}


async def main():
    uri = "ws://localhost:8080/ws?session_id=agent_test_session"
    print(f"Connecting to {uri}")

    async with websockets.connect(uri, open_timeout=15) as ws:
        # Wait for session_created
        msg = await asyncio.wait_for(ws.recv(), timeout=20)
        data = json.loads(msg)
        assert data.get("type") == "session_created", f"Got: {data}"
        print(f"Session: {data.get('session_id')} ✓\n")

        results = {}

        # TEST 1: Story Agent (triggers tell_story function call)
        results["story"] = await send_and_collect(
            ws,
            "Tell me an Ashanti Anansi trickster story.",
            "Story Agent (Anansi)",
            timeout=60,
        )

        # TEST 2: Riddle Agent (triggers pose_riddle function call)
        results["riddle"] = await send_and_collect(
            ws,
            "Give me a Swahili riddle.",
            "Riddle Agent (Swahili)",
            timeout=60,
        )

        # TEST 3: Cultural Context Agent
        results["cultural"] = await send_and_collect(
            ws,
            "What is the cultural significance of Anansi the Spider in Ashanti tradition?",
            "Cultural Context Agent",
            timeout=60,
        )

        # TEST 4: Interrupt
        results["interrupt"] = await test_interrupt(ws, "Interrupt Handling")

        # TEST 5: Multi-turn — follow-up question
        results["followup"] = await send_and_collect(
            ws,
            "Can you tell me another proverb, this time from Zulu tradition?",
            "Multi-turn Follow-up",
            timeout=60,
        )

        # SUMMARY
        print(f"\n{'#'*60}")
        print(f"AGENT TEST SUMMARY")
        print(f"{'#'*60}")
        for name, r in results.items():
            if isinstance(r, dict) and "has_response" in r:
                status = "PASS ✓" if r["has_response"] else "FAIL ✗"
                print(f"  {name:15s} {status}  msgs={r['total_msgs']:>4d}  audio={r['audio_chunks']:>4d}  text={r['text_chunks']:>2d}")
            elif isinstance(r, dict) and "before" in r:
                status = "PASS ✓" if r.get("has_interrupted") or r.get("after", 0) >= 0 else "FAIL ✗"
                print(f"  {name:15s} {status}  before={r['before']}  after={r['after']}")
        print(f"{'#'*60}")


if __name__ == "__main__":
    asyncio.run(main())
