"""The customer's phone in the demo. The agent's verification call rings here; the
phone page in the dashboard answers it, plays the agent's voice and sends back what
the customer said. One call at a time, like a real handset.
"""
import threading

from src.agent import voice

RING_TIMEOUT = 45      # seconds before an unanswered call counts as no answer
REPLY_TIMEOUT = 180    # seconds the agent waits for each answer


class PhoneLine:
    def __init__(self):
        self.lock = threading.Lock()
        self._cv = threading.Condition(self.lock)
        self._reset()
        self.call_no = 0

    def _reset(self):
        self.state = "idle"           # idle, ringing, in_call, ended
        self.case_id = None
        self.account_id = None
        self.language = "English"
        self.lines = []               # {"who": agent|customer|system, "text", "audio"}
        self.waiting = False          # the agent is waiting for the customer to speak
        self._answer = None           # True/False from the ringing screen
        self._reply = None            # what the customer said, or "" for hang-up

    # ---------------------------------------------------------------- agent side
    def dial(self, case_id: int, account_id: str, language: str) -> "Call":
        with self.lock:
            self._reset()
            self.call_no += 1
            self.case_id, self.account_id, self.language = case_id, account_id, language
        return Call(self)

    def _line(self, who, text, speak=False):
        audio = voice.speak(text, self.language) if speak else None
        with self.lock:
            self.lines.append({"who": who, "text": text,
                               "audio": f"/api/audio/{audio}" if audio else None})

    def _ring(self) -> bool:
        with self.lock:
            self.state = "ringing"
            self._cv.wait_for(lambda: self._answer is not None, timeout=RING_TIMEOUT)
            ok = bool(self._answer)
            self.state = "in_call" if ok else "ended"
        if not ok:
            self._line("system", "The customer did not answer.")
        return ok

    def _ask(self, text: str):
        self._line("agent", text, speak=True)
        with self.lock:
            if self.state != "in_call":
                return None
            self._reply, self.waiting = None, True
            self._cv.wait_for(lambda: self._reply is not None, timeout=REPLY_TIMEOUT)
            reply, self.waiting = self._reply, False
            if not reply:
                self.state = "ended"
        if not reply:
            self._line("system", "The customer hung up." if reply == "" else "No reply; the call timed out.")
            return None
        return reply

    def _hang_up(self):
        if self.state == "in_call":
            self._line("agent", voice.LANGUAGES[self.language]["bye"], speak=True)
        with self.lock:
            self.state, self.waiting = "ended", False
            self._cv.notify_all()

    # ---------------------------------------------------------------- phone page side
    def answer(self, accept: bool) -> bool:
        with self.lock:
            if self.state != "ringing":
                return False
            self._answer = accept
            self._cv.notify_all()
            return True

    def reply(self, text: str) -> bool:
        with self.lock:
            if not self.waiting:
                return False
            self._reply = text.strip()
            self.lines.append({"who": "customer", "text": text.strip(), "audio": None})
            self._cv.notify_all()
            return True

    def hang_up_from_phone(self) -> None:
        with self.lock:
            if self.state == "ringing":
                self._answer = False
            elif self.waiting:
                self._reply = ""
            self.state = "ended"
            self._cv.notify_all()

    def snapshot(self) -> dict:
        with self.lock:
            return {"call_no": self.call_no, "state": self.state, "case_id": self.case_id,
                    "account_id": self.account_id, "language": self.language,
                    "code": voice.LANGUAGES[self.language]["code"],
                    "waiting": self.waiting, "lines": list(self.lines)}


class Call:
    """What the agent holds during a call (the investigator's Phone protocol)."""

    def __init__(self, line: PhoneLine):
        self.line, self.started = line, False

    def ask(self, text: str):
        if not self.started:
            self.started = True
            if not self.line._ring():
                return None
            self.line._line("agent", voice.LANGUAGES[self.line.language]["intro"], speak=True)
        return self.line._ask(text)

    def hang_up(self):
        self.line._hang_up()
