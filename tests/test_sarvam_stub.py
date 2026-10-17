"""Sarvam path against a local stand-in server (request shape, rate limit, speech cache, provider routing).
Proves our handling of Sarvam's documented API, not Sarvam's models.
    python -m unittest tests.test_sarvam_stub -v"""
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from flask import Flask, jsonify, request
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PORT = 5097
for k in ("WATSONX_API_KEY", "WATSONX_PROJECT_ID"):
    os.environ.pop(k, None)
os.environ.update({
    "VIDYASETU_NO_DOTENV": "1", "VIDYASETU_MODE": "auto", "VIDYASETU_DATA": tempfile.mkdtemp(), "VECTOR_BACKEND": "sqlite",
    "SARVAM_API_KEY": "sk-fake", "SARVAM_BASE_URL": f"http://127.0.0.1:{PORT}", "SARVAM_RPM": "600", "SARVAM_TTS_RPM": "600",
})

from vidyasetu import mock  # noqa: E402

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 30
stub = Flask("sarvam-stub")
SEEN = {"chat": [], "tts": 0, "bad_requests": []}


@stub.post("/v1/chat/completions")
def chat():
    if request.headers.get("api-subscription-key") != "sk-fake":
        return jsonify({"error": {"code": "invalid_api_key_error"}}), 403
    b = request.get_json()
    if b["model"] != "sarvam-105b" or "reasoning_effort" not in b or b["reasoning_effort"] is not None or b["max_tokens"] > 4096:
        SEEN["bad_requests"].append({k: b.get(k) for k in ("model", "reasoning_effort", "max_tokens")})
    SEEN["chat"].append(b.get("response_format"))
    system, user = b["messages"][0]["content"], b["messages"][1]["content"]
    if "school teacher. Rewrite" in system:
        out = user
    elif "numbered items" in system:
        out = re.sub(r"<<(\d+)>> ", lambda m: f"<<{m.group(1)}>> HI: ", user)
    elif "expert translator of school textbooks" in system:
        out = "HI: " + user
    elif "Translate the following" in system:
        out = "<think>hmm</think>" + user.replace("HI: ", "")
    elif "multiple-choice" in system:
        assert b.get("response_format") == {"type": "json_object"}
        out = mock._quiz(user, 2)
    elif "ONLY the textbook excerpts" in system:
        out = "Chlorophyll is the green substance in leaves (p. 1)."
    else:
        out = "unexpected prompt"
    return jsonify({"choices": [{"message": {"role": "assistant", "content": out}, "finish_reason": "stop"}]})


@stub.post("/text-to-speech")
def tts():
    if request.headers.get("api-subscription-key") != "sk-fake":
        return jsonify({}), 403
    b = request.get_json()
    assert b["language_code"] in ("hi-IN", "mr-IN") and b["model"] == "bulbul:v3" and b["speaker"] and b["text"]
    assert "photosynthesis" not in b["text"]  # English helper term stripped before speaking
    SEEN["tts"] += 1
    enc = base64.b64encode(WAV).decode()
    cut = len(enc) // 2
    return jsonify({"request_id": "x", "audios": [enc[:cut], enc[cut:]]})  # slices of ONE base64 string, as documented


class SarvamFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = make_server("127.0.0.1", PORT, stub, threaded=True)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        import app as appmod
        cls.c = appmod.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def wait(self, lid):
        for _ in range(100):
            d = self.c.get(f"/api/lessons/{lid}").get_json()
            if d["status"] != "running":
                return d
            time.sleep(0.2)
        raise TimeoutError

    def test_config_reports_sarvam(self):
        cfg = self.c.get("/api/config").get_json()
        self.assertEqual(cfg["mode"], "live")
        self.assertEqual(cfg["label"], "Live · Sarvam")
        self.assertEqual(cfg["providers"], {"generate": "sarvam", "translate": "sarvam", "embed": "local", "speech": "sarvam"})
        self.assertTrue(next(l for l in cfg["languages"] if l["code"] == "hi")["neural_tts"])
        self.assertFalse(next(l for l in cfg["languages"] if l["code"] == "ur")["neural_tts"])

    def test_lesson_ask_quiz_with_sarvam_only(self):
        text = "Chlorophyll captures energy from sunlight. It is found in leaves of the plant."
        lid = self.c.post("/api/lessons", data={"lang": "hi", "grade": "7", "text": text}).get_json()["id"]
        d = self.wait(lid)
        self.assertEqual(d["status"], "done", d.get("error"))
        p = d["paragraphs"][0]
        self.assertIn("पर्णहरित (chlorophyll)", p["translated"])
        self.assertEqual(p["quality"]["status"], "ok")            # back-translation <think> block stripped, local embedder scored it
        self.assertTrue(0.5 <= p["quality"]["score"] <= 1.0)
        r = self.c.post(f"/api/lessons/{lid}/ask", json={"question": "What is chlorophyll?"}).get_json()
        self.assertTrue(r["found"]); self.assertIn("पर्णहरित", r["answer"])
        q = self.c.post(f"/api/lessons/{lid}/quiz", json={"n": 2}).get_json()["questions"]
        self.assertTrue(1 <= len(q) <= 2)
        self.assertEqual(SEEN["bad_requests"], [])                 # model id, thinking off, max_tokens within plan cap
        self.assertIn({"type": "json_object"}, SEEN["chat"])

    def test_speech_endpoint_caches_and_validates(self):
        before = SEEN["tts"]
        body = {"lang": "hi", "text": "पौधे अपना भोजन स्वयं बनाते हैं। इस प्रक्रिया को प्रकाशसंश्लेषण (photosynthesis) कहते हैं।"}
        r1 = self.c.post("/api/tts", json=body)
        self.assertEqual((r1.status_code, r1.mimetype), (200, "audio/wav"))
        self.assertEqual(r1.data, WAV)                             # two base64 pieces were joined, then decoded
        self.assertEqual(self.c.post("/api/tts", json=body).data, WAV)
        self.assertEqual(SEEN["tts"] - before, 1)                  # second request served from cache: no extra credits spent
        self.assertEqual(self.c.post("/api/tts", json={"lang": "ur", "text": "ہیلو"}).status_code, 400)
        self.assertEqual(self.c.post("/api/tts", json={"lang": "hi", "text": "  "}).status_code, 400)

    def test_bad_key_reported_cleanly(self):
        from vidyasetu import config, sarvam
        good = config.SARVAM_API_KEY
        config.SARVAM_API_KEY = "wrong"
        try:
            with self.assertRaises(sarvam.SarvamError) as cm:
                sarvam.chat([{"role": "user", "content": "hi"}])
            self.assertIn("rejected the API key", str(cm.exception))
        finally:
            config.SARVAM_API_KEY = good


class Throttling(unittest.TestCase):
    def test_throttle_spaces_calls(self):
        from vidyasetu.sarvam import Throttle
        t = Throttle(2, window=0.5)
        start = time.monotonic()
        for _ in range(3):
            t.wait()
        self.assertGreaterEqual(time.monotonic() - start, 0.45)    # 3rd call had to wait for the window to slide


class Routing(unittest.TestCase):
    def providers(self, **env):
        clean = {k: v for k, v in os.environ.items() if not k.startswith(("WATSONX_", "SARVAM_", "GEN_P", "TRANSLATE_P", "EMBED_P", "SPEECH_P"))}
        clean.update({"VIDYASETU_NO_DOTENV": "1", "VIDYASETU_DATA": tempfile.mkdtemp(), **env})
        out = subprocess.check_output(
            [sys.executable, "-c", "import json;from vidyasetu import config as c;print(json.dumps([c.GEN_PROVIDER,c.TRANSLATE_PROVIDER,c.EMBED_PROVIDER,c.SPEECH_PROVIDER,c.MOCK,c.QUALITY_THRESHOLD]))"],
            env=clean, cwd=ROOT)
        return json.loads(out)

    def test_routing(self):
        both = {"WATSONX_API_KEY": "k", "WATSONX_PROJECT_ID": "p", "SARVAM_API_KEY": "s"}
        self.assertEqual(self.providers(**both), ["watsonx", "sarvam", "watsonx", "sarvam", False, 0.8])   # the hybrid
        self.assertEqual(self.providers(SARVAM_API_KEY="s"), ["sarvam", "sarvam", "local", "sarvam", False, 0.5])
        self.assertEqual(self.providers(WATSONX_API_KEY="k", WATSONX_PROJECT_ID="p"), ["watsonx", "watsonx", "watsonx", "browser", False, 0.8])
        self.assertEqual(self.providers(), ["mock", "mock", "local", "browser", True, 0.5])
        self.assertEqual(self.providers(**both, GEN_PROVIDER="sarvam")[:2], ["sarvam", "sarvam"])
        self.assertEqual(self.providers(**both, VIDYASETU_MODE="mock"), ["mock", "mock", "local", "browser", True, 0.5])
        self.assertEqual(self.providers(**both, SPEECH_PROVIDER="browser")[3], "browser")


if __name__ == "__main__":
    unittest.main()
