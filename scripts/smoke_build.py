"""Check the frozen CLI and signaling server without a graphical desktop."""

import json
import os
import secrets
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


def main() -> None:
    executable = Path("dist/rdc.exe" if os.name == "nt" else "dist/rdc").resolve()
    # Run outside the source tree so local modules cannot mask missing imports.
    with tempfile.TemporaryDirectory() as cwd:
        for arguments in (["--help"], ["version"]):
            subprocess.run(
                [str(executable), *arguments], cwd=cwd, check=True, timeout=60
            )

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = {**os.environ, "RDC_SIGNALING_TOKEN": secrets.token_hex(32)}
        server = subprocess.Popen(
            [str(executable), "serve", "--host", "127.0.0.1", "--port", str(port)],
            cwd=cwd,
            env=env,
        )
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError(f"Frozen server exited with {server.returncode}")
                try:
                    with urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=2
                    ) as response:
                        if json.load(response) != {"status": "ok"}:
                            raise RuntimeError("Unexpected health response")
                    print("Frozen server health check passed.")
                    break
                except (URLError, TimeoutError):
                    time.sleep(0.25)
            else:
                raise RuntimeError(
                    "Frozen server did not become healthy within 60 seconds"
                )
        finally:
            if os.name == "nt":
                # A PyInstaller onefile executable has a bootloader parent and
                # an application child. terminate() only kills the parent on
                # Windows, leaving the child holding the temporary cwd open.
                # Kill the tree while the parent still exists to identify it.
                if server.poll() is None:
                    subprocess.run(
                        ["taskkill", "/PID", str(server.pid), "/T", "/F"],
                        check=True,
                        timeout=30,
                    )
                server.wait(timeout=10)
            else:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=10)


if __name__ == "__main__":
    main()
