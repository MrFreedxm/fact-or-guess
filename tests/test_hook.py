"""Feeds the hook a transcript the way Claude Code would and checks the decision.
Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fact-or-guess.py")


def run(payload, env=None):
    e = dict(os.environ); e.update(env or {})
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True, text=True, env=e)
    return p.returncode, p.stdout, p.stderr

def transcript(tmp, records):
    proj = os.path.join(tmp, "proj"); os.makedirs(os.path.join(proj, "memory"), exist_ok=True)
    with open(os.path.join(proj, "memory", "capability-night-bot.md"), "w") as f:
        f.write("# night bot\n")
    path = os.path.join(proj, "sess1.jsonl")
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


def user(text):
    return {"type": "user", "message": {"content": text}}


def assistant(blocks):
    return {"type": "assistant", "message": {"content": blocks}}


class FactGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = {"FACT_GATE_LOG": os.path.join(self.tmp, "log.jsonl")}

    def test_claim_without_source_is_blocked(self):
        t = transcript(self.tmp, [user("how is it set up?"),
                                  assistant([{"type": "text", "text": "The night bot listens on port 8100 and there is no backup."}])])
        rc, so, se = run({"transcript_path": t}, self.env)
        self.assertEqual(rc, 2)
        self.assertIn("Fact or guess", se)

    def test_claim_with_source_passes(self):
        t = transcript(self.tmp, [user("how is it set up?"),
                                  assistant([{"type": "tool_use", "name": "Read", "input": {"file_path": "/x/capability-night-bot.md"}}]),
                                  assistant([{"type": "text", "text": "The night bot listens on port 8100."}])])
        rc, so, se = run({"transcript_path": t}, self.env)
        self.assertEqual(rc, 0)

    def test_hedged_claim_passes(self):
        t = transcript(self.tmp, [user("how is it set up?"),
                                  assistant([{"type": "text", "text": "From memory the night bot listens on 8100, I did not check."}])])
        rc, so, se = run({"transcript_path": t}, self.env)
        self.assertEqual(rc, 0)

    def test_source_earlier_in_session_counts(self):
        t = transcript(self.tmp, [user("open the bot file"),
                                  assistant([{"type": "tool_use", "name": "Read", "input": {"file_path": "/x/capability-night-bot.md"}}]),
                                  assistant([{"type": "text", "text": "Opened."}]),
                                  user("so what does it do?"),
                                  assistant([{"type": "text", "text": "capability-night-bot answers at night."}])])
        rc, so, se = run({"transcript_path": t}, self.env)
        self.assertEqual(rc, 0)




if __name__ == "__main__":
    unittest.main()
