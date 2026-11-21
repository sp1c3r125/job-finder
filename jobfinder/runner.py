"""One background job at a time (a search run, a resume import or a tailor). In-process thread, no queue."""
import threading


class Runner:
    def __init__(self):
        self._lock = threading.Lock()
        self.state = {"state": "idle", "label": "", "message": ""}

    def start(self, label, fn):
        """Run fn() in a thread. Returns False (and does nothing) if something is already running."""
        if not self._lock.acquire(blocking=False):
            return False
        self.state = {"state": "running", "label": label, "message": ""}

        def work():
            try:
                msg = fn()
                self.state = {"state": "done", "label": label, "message": str(msg or "")[:300]}
            except BaseException as e:  # noqa: BLE001 - SystemExit from get_backend must not vanish
                self.state = {"state": "failed", "label": label, "message": f"{type(e).__name__}: {e}"[:300]}
            finally:
                self._lock.release()

        threading.Thread(target=work, daemon=True).start()
        return True

    def status(self):
        return dict(self.state)
