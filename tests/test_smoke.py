"""End-to-end smoke test in demo mode:  python -m unittest tests.test_smoke -v"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["VIDYASETU_NO_DOTENV"] = "1"
os.environ["VIDYASETU_MODE"] = "mock"
os.environ["VIDYASETU_DATA"] = tempfile.mkdtemp()

import app as appmod  # noqa: E402
from vidyasetu import glossary  # noqa: E402


def wait_done(client, lid, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        data = client.get(f"/api/lessons/{lid}").get_json()
        if data["status"] in ("done", "error"):
            return data
        time.sleep(0.2)
    raise TimeoutError


class Smoke(unittest.TestCase):
    def setUp(self):
        self.c = appmod.app.test_client()

    def test_glossary_lock_roundtrip(self):
        locked, m = glossary.lock("Photosynthesis needs carbon dioxide and oxygen. Oxygen is released.", "hi")
        self.assertEqual(len(m), 3)
        self.assertNotIn("xygen", locked)
        out = glossary.unlock(locked, m, "hi")
        self.assertIn("प्रकाशसंश्लेषण (photosynthesis)", out)
        self.assertEqual(out.count("(oxygen)"), 1)  # english shown only on first use

    def test_sample_lesson_hindi_and_marathi(self):
        for lang, needle in (("hi", "प्रकाशसंश्लेषण (photosynthesis)"), ("mr", "हरितद्रव्य (chlorophyll)")):
            lid = self.c.post("/api/lessons/sample", json={"lang": lang}).get_json()["id"]
            data = wait_done(self.c, lid)
            self.assertEqual(data["status"], "done", data.get("error"))
            self.assertEqual(len(data["paragraphs"]), 4)
            joined = " ".join(p["translated"] for p in data["paragraphs"])
            self.assertIn(needle, joined)
            self.assertNotIn("[[T", joined)
            self.assertNotIn("demo]", joined)

    def test_ask_quiz_results(self):
        lid = self.c.post("/api/lessons/sample", json={"lang": "hi"}).get_json()["id"]
        wait_done(self.c, lid)
        r = self.c.post(f"/api/lessons/{lid}/ask", json={"question": "What is chlorophyll?"}).get_json()
        self.assertTrue(r["found"])
        self.assertIn("green substance", r["answer_en"])
        self.assertIn("पर्णहरित", r["answer"])
        self.assertTrue(r["sources"])
        r2 = self.c.post(f"/api/lessons/{lid}/ask", json={"question": "प्रकाशसंश्लेषण क्या है?"}).get_json()
        self.assertTrue(r2["found"])
        r3 = self.c.post(f"/api/lessons/{lid}/ask", json={"question": "Who won the cricket world cup?"}).get_json()
        self.assertFalse(r3["found"])
        q = self.c.post(f"/api/lessons/{lid}/quiz", json={"n": 3}).get_json()["questions"]
        self.assertEqual(len(q), 3)
        for item in q:
            self.assertEqual(len(item["options"]), 4)
            self.assertTrue(item["question"]["local"])
        self.assertEqual(self.c.post(f"/api/lessons/{lid}/results", json={"score": 2, "total": 3}).status_code, 200)
        self.assertEqual(self.c.get(f"/api/lessons/{lid}/results").get_json()[0]["score"], 2)

    def test_upload_text_file_and_teacher(self):
        import io
        data = {"lang": "mr", "grade": "6", "file": (io.BytesIO(b"Energy is the ability to do work. Force changes motion."), "notes.txt")}
        r = self.c.post("/api/lessons", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 202)
        done = wait_done(self.c, r.get_json()["id"])
        self.assertEqual(done["status"], "done")
        t = self.c.post("/api/teacher", json={"text": "Plants make their own food. This process is called photosynthesis.", "langs": ["hi", "mr"]})
        self.assertEqual(t.status_code, 200)
        self.assertIn("प्रकाशसंश्लेषण", t.get_json()["hi"]["text"])

    def test_delete_lesson(self):
        lid = self.c.post("/api/lessons/sample", json={"lang": "hi"}).get_json()["id"]
        wait_done(self.c, lid)
        self.c.post(f"/api/lessons/{lid}/results", json={"score": 1, "total": 2})
        self.assertIn(lid, [l["id"] for l in self.c.get("/api/lessons").get_json()])
        self.assertEqual(self.c.delete(f"/api/lessons/{lid}").status_code, 200)
        self.assertEqual(self.c.get(f"/api/lessons/{lid}").status_code, 404)
        self.assertNotIn(lid, [l["id"] for l in self.c.get("/api/lessons").get_json()])
        self.assertEqual(self.c.get(f"/api/lessons/{lid}/results").get_json(), [])
        self.assertEqual(self.c.delete(f"/api/lessons/{lid}").status_code, 404)
        self.assertEqual(self.c.delete("/api/lessons/not-a-real-id").status_code, 404)

    def test_bad_input(self):
        self.assertEqual(self.c.post("/api/lessons", data={"lang": "xx"}).status_code, 400)
        self.assertEqual(self.c.post("/api/lessons", data={"lang": "hi"}).status_code, 400)
        self.assertEqual(self.c.get("/api/lessons/nope").status_code, 404)

    def test_sample_in_all_ten_languages(self):
        from vidyasetu import languages
        for lang in languages.LANGUAGES:
            lid = self.c.post("/api/lessons/sample", json={"lang": lang}).get_json()["id"]
            data = wait_done(self.c, lid)
            self.assertEqual(data["status"], "done", data.get("error"))
            joined = " ".join(p["translated"] for p in data["paragraphs"])
            self.assertNotIn("demo]", joined, lang)
            self.assertNotIn("[[T", joined, lang)
            self.assertIn("(photosynthesis)", joined, lang)  # glossary lock: "native (english)"
            q = self.c.post(f"/api/lessons/{lid}/quiz", json={"n": 5}).get_json()["questions"]
            self.assertTrue(q)
            for item in q:
                self.assertNotIn("demo]", item["question"]["local"] + "".join(o["local"] for o in item["options"]), lang)

    def test_not_in_textbook_reply_is_translated(self):
        lid = self.c.post("/api/lessons/sample", json={"lang": "ta"}).get_json()["id"]
        wait_done(self.c, lid)
        r = self.c.post(f"/api/lessons/{lid}/ask", json={"question": "Who won the cricket world cup?"}).get_json()
        self.assertFalse(r["found"])
        self.assertEqual(r["sources"], [])
        self.assertNotIn("demo]", r["answer"])

    def test_config_and_health(self):
        cfg = self.c.get("/api/config").get_json()
        self.assertEqual(cfg["mode"], "demo")
        self.assertEqual(len(cfg["languages"]), 10)
        self.assertEqual(self.c.get("/healthz").get_json()["ok"], True)

    def test_teacher_caps_at_six_languages(self):
        langs = ["hi", "mr", "bn", "ta", "te", "gu", "kn", "ml"]
        t = self.c.post("/api/teacher", json={"text": "Plants make their own food.", "langs": langs}).get_json()
        self.assertEqual(len(t), 6)
        self.assertEqual(self.c.post("/api/teacher", json={"text": "", "langs": ["hi"]}).status_code, 400)
        self.assertEqual(self.c.post("/api/teacher", json={"text": "x", "langs": ["xx"]}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
