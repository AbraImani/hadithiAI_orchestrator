"""
Live WebSocket test — send text, collect Gemini responses.
Run:  python tests/test_ws_live.py
"""
import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    print("pip install websockets")
    sys.exit(1)


async def test_websocket():
    uri = "ws://localhost:8080/ws?session_id=test_ws_live"
    print(f"Connecting to {uri} ...")

    try:
        async with websockets.connect(uri, open_timeout=15) as ws:
            # 1. Expect session_created
            msg = await asyncio.wait_for(ws.recv(), timeout=20)
            data = json.loads(msg)
            print(f"[1] RECEIVED: type={data.get('type')}, session_id={data.get('session_id')}")
            assert data.get("type") == "session_created", f"Expected session_created, got {data}"

            # 2. Send text input
            payload = {
                "type": "text_input",
                "data": "Hello! Tell me a famous Yoruba proverb and its meaning.",
                "seq": 1,
            }
            await ws.send(json.dumps(payload))
            print("[2] SENT: text_input")

            # 3. Collect responses
            responses = []
            audio_count = 0
            text_count = 0
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=45)
                    evt = json.loads(raw)
                    responses.append(evt)
                    t = evt.get("type", "?")

                    if t == "audio_chunk":
                        audio_count += 1
                        if audio_count <= 3 or audio_count % 10 == 0:
                            sz = len(evt.get("data", ""))
                            print(f"  [audio_chunk] #{audio_count}, b64_len={sz}")
                    elif t == "text_chunk":
                        text_count += 1
                        txt = evt.get("data", "")[:150]
                        print(f"  [text_chunk] #{text_count}: {txt}")
                    elif t == "turn_end":
                        print(f"  [turn_end] ✓")
                        break
                    elif t == "error":
                        print(f"  [error] {evt.get('error', evt)}")
                        break
                    elif t == "interrupted":
                        print(f"  [interrupted]")
                    elif t == "agent_state":
                        print(f"  [agent_state] agent={evt.get('agent')}, state={evt.get('state')}")
                    elif t == "image_ready":
                        print(f"  [image_ready] url={evt.get('url','')[:80]}")
                    elif t == "pong":
                        pass
                    else:
                        print(f"  [{t}] {json.dumps(evt)[:120]}")
            except asyncio.TimeoutError:
                print(f"  ⏱ Timeout after {len(responses)} messages")

            # 4. Summary
            print(f"\n{'='*50}")
            print(f"Total messages:  {len(responses)}")
            print(f"Audio chunks:    {audio_count}")
            print(f"Text chunks:     {text_count}")
            print(f"Pipeline:        {'WORKING' if audio_count > 0 or text_count > 0 else 'NO RESPONSE'}")
            print(f"{'='*50}")

            # 5. Test ping
            await ws.send(json.dumps({"type": "ping", "seq": 99}))
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            pong = json.loads(raw)
            assert pong.get("type") == "pong", f"Expected pong, got {pong}"
            print("Ping/Pong: ✓")

    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_websocket())
