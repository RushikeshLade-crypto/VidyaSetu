"""Exercises the LIVE code path (watsonx REST client, verify step, quiz JSON parsing, token handling)
against a local stand-in for watsonx.ai.  It proves our request/response handling, not IBM's models.
    python -m unittest tests.test_live_stub -v"""
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from flask import Flask, jsonify, request
from werkzeug.serving import make_server

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
PORT = 5098
os.environ.update({
    "VIDYASETU_NO_DOTENV": "1", "VIDYASETU_MODE": "live", "VIDYASETU_DATA": tempfile.mkdtemp(), "VECTOR_BACKEND": "sqlite",
    "WATSONX_API_KEY": "fake-key", "WATSONX_PROJECT_ID": "proj-123",
    "WATSONX_URL": f"http://127.0.0.1:{PORT}", "IBM_IAM_URL": f"http://127.0.0.1:{PORT}/identity/token",
})

from vidyasetu import mock  # noqa: E402

stub = Flask("stub")
SEEN = {"tokens": 0, "chat": [], "embed": 0, "flaky": 0}


@stub.post("/identity/token")
def token():
    assert request.form["grant_type"] == "urn:ibm:params:oauth:grant-type:apikey"
    assert request.form["apikey"] == "fake-key"
    SEEN["tokens"] += 1
    return jsonify({"access_token": "tok", "expiration": time.time() + 3600})


@stub.post("/ml/v1/text/chat")
def chat():
    assert request.headers["Authorization"] == "Bearer tok"
    assert "version" in request.args
    b = request.get_json()
    assert b["project_id"] == "proj-123" and b["model_id"] and b["messages"][0]["role"] == "system"
    SEEN["chat"].append(b["model_id"])
    system, user = b["messages"][0]["content"], b["messages"][1]["content"]
    if "school teacher. Rewrite" in system:
        out = user
    elif "numbered items" in system:  # translate_many
        out = re.sub(r"<<(\d+)>> ", lambda m: f"<<{m.group(1)}>> HI: ", user)
    elif "expert translator of school textbooks" in system:
        if SEEN["flaky"] == 0:  # first attempt drops the locked tokens -> pipeline must retry
            SEEN["flaky"] = 1
            out = "HI: " + re.sub(r"\[\[T\d+\]\]", "", user)
        else:
            out = "HI: " + user
    elif "Translate the following" in system:  # back translation
        out = user.replace("HI: ", "")
    elif "write" in system and "quiz" in system:
        out = "Sure!\n```json\n" + mock._quiz(user, 2) + "\n```"
    elif "ONLY the textbook excerpts" in system or "ONLY the" in system:
        out = "NOT_IN_TEXTBOOK" if "cricket" in user else "Chlorophyll is the green substance in leaves (p. 1)."
    else:
        out = "unexpected prompt"
    return jsonify({"choices": [{"message": {"role": "assistant", "content": out}}]})


@stub.post("/ml/v1/text/embeddings")
def embeddings():
    b = request.get_json()
    assert b["model_id"] and b["project_id"] == "proj-123"
    SEEN["embed"] += 1
    return jsonify({"results": [{"embedding": e} for e in mock.embed(b["inputs"])]})


class Live(unittest.TestCase):
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

    def test_full_live_flow(self):
        self.assertEqual(self.c.get("/api/config").get_json()["mode"], "live")
        text = "Chlorophyll captures energy from sunlight. It is found in leaves of the plant."
        lid = self.c.post("/api/lessons", data={"lang": "hi", "grade": "7", "text": text}).get_json()["id"]
        d = self.wait(lid)
        self.assertEqual(d["status"], "done", d.get("error"))
        p = d["paragraphs"][0]
        self.assertIn("पर्णहरित (chlorophyll)", p["translated"])      # tokens survived the retry and were unlocked
        self.assertEqual(p["quality"]["status"], "ok")                   # back-translation + embedding check ran
        self.assertGreaterEqual(p["quality"]["score"], 0.8)             # stub only prefixes text, so score is not ~1.0
        self.assertEqual(SEEN["tokens"], 1)                              # IAM token fetched once and cached
        r = self.c.post(f"/api/lessons/{lid}/ask", json={"question": "What is chlorophyll?"}).get_json()
        self.assertTrue(r["found"]); self.assertIn("पर्णहरित", r["answer"])
        r = self.c.post(f"/api/lessons/{lid}/ask", json={"question": "Who won the cricket match?"}).get_json()
        self.assertFalse(r["found"])
        q = self.c.post(f"/api/lessons/{lid}/quiz", json={"n": 2}).get_json()["questions"]  # JSON inside ```fences```
        self.assertTrue(1 <= len(q) <= 2)

    def test_auth_failure_is_reported(self):
        from vidyasetu import watsonx
        old = watsonx.config.WATSONX_API_KEY
        watsonx._token["value"] = None
        watsonx.config.WATSONX_API_KEY = "wrong"
        try:
            with self.assertRaises(Exception):
                watsonx.chat("m", [{"role": "user", "content": "x"}])
        finally:
            watsonx.config.WATSONX_API_KEY = old
            watsonx._token["value"] = None


if __name__ == "__main__":
    unittest.main()
