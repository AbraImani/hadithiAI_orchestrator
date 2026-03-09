"""
Focused test: interrupt + follow-up.
Tests that after interrupting a response, a new question gets answered.
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


async def main():
    uri = "ws://localhost:8080/ws?session_id=interrupt_test"
    print(f"Connecting to {uri}")

    async with websockets.connect(uri, open_timeout=15) as ws:
        # Wait for session_created
        msg = await asyncio.wait_for(ws.recv(), timeout=20)
        data = json.loads(msg)
        assert data.get("type") == "session_created", f"Got: {data}"
        print(f"Session created OK")

        # Step 1: Send a request that triggers a long response
        print("\n--- Step 1: Request long story ---")
        await ws.send(json.dumps({
            "type": "text_input",
            "data": "Tell me a very long Zulu creation myth with lots of detail.",
            "seq": 1,
        }))

        # Collect for 3 seconds
        count = 0
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=3)
                evt = json.loads(raw)
                t = evt.get("type", "?")
                if t == "audio_chunk":
                    count += 1
                elif t == "text_chunk":
                    print(f"  [text] {evt.get('data', '')[:100]}")
                elif t == "turn_end":
                    print(f"  [turn_end] arrived before interrupt!")
                    break
        except asyncio.TimeoutError:
            pass
        print(f"  Got {count} audio chunks before interrupt")

        # Step 2: Send interrupt
        print("\n--- Step 2: Interrupt ---")
        await ws.send(json.dumps({"type": "interrupt", "seq": 2}))
        
        # Wait a bit for the interrupt to be processed
        await asyncio.sleep(2)
        
        # Drain any remaining messages from the interrupt
        drained = 0
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=2)
                evt = json.loads(raw)
                t = evt.get("type", "?")
                drained += 1
                if t == "interrupted":
                    print(f"  [interrupted] received")
                elif t == "turn_end":
                    print(f"  [turn_end] after interrupt")
                elif t == "error":
                    print(f"  [error] {evt.get('message', '')[:80]}")
        except asyncio.TimeoutError:
            pass
        print(f"  Drained {drained} messages after interrupt")

        # Step 3: Send follow-up
        print("\n--- Step 3: Follow-up question ---")
        await ws.send(json.dumps({
            "type": "text_input",
            "data": "Tell me a short Yoruba proverb.",
            "seq": 3,
        }))

        # Collect response
        audio = 0
        text_parts = []
        got_turn_end = False
        t0 = time.time()
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=90)
                evt = json.loads(raw)
                t = evt.get("type", "?")
                if t == "audio_chunk":
                    audio += 1
                elif t == "text_chunk":
                    txt = evt.get("data", "")
                    text_parts.append(txt)
                    print(f"  [text] {txt[:120]}")
                elif t == "turn_end":
                    got_turn_end = True
                    break
                elif t == "error":
                    print(f"  [error] {evt.get('message', '')[:100]}")
                    break
        except asyncio.TimeoutError:
            print(f"  TIMEOUT waiting for response")

        elapsed = time.time() - t0
        print(f"\n  Result: audio={audio}, text={len(text_parts)}, turn_end={got_turn_end}, {elapsed:.1f}s")

        if audio > 0 or len(text_parts) > 0:
            print("\n  >>> FOLLOW-UP AFTER INTERRUPT: PASS <<<")
        else:
            print("\n  >>> FOLLOW-UP AFTER INTERRUPT: FAIL <<<")


if __name__ == "__main__":
    asyncio.run(main())
