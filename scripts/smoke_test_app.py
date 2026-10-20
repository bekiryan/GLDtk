"""Smoke test for a packaged app's backend: /health answers, and it exits with its parent.

    python scripts/smoke_test_app.py <path to the bundled gldtk-server(.exe)>
"""
import subprocess
import sys
import time
import urllib.request

PORT = 8799


def main(exe: str) -> int:
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    server = subprocess.Popen([exe, "--port", str(PORT), "--parent-pid", str(parent.pid)])
    try:
        for _ in range(120):
            try:
                body = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2).read()
                print("health:", body.decode())
                break
            except OSError:
                time.sleep(0.5)
        else:
            print("no /health answer")
            return 1
        parent.kill()
        parent.wait()  # reaped, so the pid is really gone
        server.wait(timeout=20)
        print("server exited with its parent")
        return 0
    except subprocess.TimeoutExpired:
        print("server outlived its parent")
        return 1
    finally:
        parent.kill()
        server.kill()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
