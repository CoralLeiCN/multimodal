"""Bound task lifetime independently of the polling worker."""

import os
import threading
import time


def main():
    parent = int(os.environ["AGENT_PARENT_PID"])
    deadline = float(os.environ["AGENT_PROCESS_DEADLINE"])

    def watch():
        while True:
            if os.getppid() != parent:
                os._exit(125)
            if time.time() >= deadline:
                os._exit(124)
            time.sleep(0.2)

    threading.Thread(target=watch, daemon=True).start()
    from .runner import main as run

    return run()
