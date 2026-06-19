import unittest
import numpy as np
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import queue
import urllib.request
import zipfile
import httpx
from fastapi import HTTPException
from fastapi.datastructures import UploadFile

# Import backend logic
import main as backend

class TestPhonRecBackend(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="phonrec_test_")
        self.workspace_dir = os.path.join(self.temp_dir, "workspace")
        backend.configure_workspace(self.workspace_dir)
        backend.configure_session_token("test-token")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_health_and_authentication(self):
        import asyncio

        async def run_requests():
            transport = httpx.ASGITransport(app=backend.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                health = await client.get("/api/health")
                unauthorized = await client.get("/api/project/state")
                authorized = await client.get(
                    "/api/project/state",
                    headers={"Authorization": "Bearer test-token"},
                )
                return health, unauthorized, authorized

        health, unauthorized, authorized = asyncio.run(run_requests())
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["protocol_version"], 1)
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(authorized.status_code, 200)

    def test_workspace_isolation(self):
        self.assertEqual(backend.WORKSPACE_DIR, os.path.abspath(self.workspace_dir))
        self.assertTrue(os.path.isdir(backend.AUDIO_DIR))
        self.assertTrue(os.path.isdir(backend.DATA_DIR))
        self.assertFalse(backend.WORKSPACE_DIR.startswith(backend.BASE_DIR))

    def test_random_loopback_port(self):
        server_socket = backend.create_server_socket(0)
        try:
            host, port = server_socket.getsockname()
            self.assertEqual(host, "127.0.0.1")
            self.assertGreater(port, 0)
        finally:
            server_socket.close()

    def test_engine_process_handshake_auth_and_termination(self):
        process_workspace = os.path.join(self.temp_dir, "process_workspace")
        environment = os.environ.copy()
        environment["PHONTRACER_SESSION_TOKEN"] = "process-token"
        process = subprocess.Popen(
            [
                sys.executable,
                backend.__file__,
                "--workspace",
                process_workspace,
                "--port",
                "0",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        lines = queue.Queue()
        reader = threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True)
        reader.start()
        try:
            handshake_line = lines.get(timeout=15)
            handshake = json.loads(handshake_line)
            self.assertEqual(handshake["event"], "ready")
            self.assertEqual(handshake["protocol_version"], 1)

            health_url = f"http://127.0.0.1:{handshake['port']}/api/health"
            with urllib.request.urlopen(health_url, timeout=5) as response:
                health = json.loads(response.read().decode("utf-8"))
            self.assertEqual(health["status"], "ok")

            state_request = urllib.request.Request(
                f"http://127.0.0.1:{handshake['port']}/api/project/state",
                headers={"Authorization": "Bearer process-token"},
            )
            with urllib.request.urlopen(state_request, timeout=5) as response:
                self.assertEqual(response.status, 200)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
        self.assertIsNotNone(process.returncode)

    def test_project_import_rejects_path_traversal(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("project.json", '{"version":"1.0"}')
            zip_file.writestr("../outside.txt", "不应写出工作区")
        archive.seek(0)
        upload = UploadFile(filename="unsafe.teproj", file=archive)

        import asyncio

        with self.assertRaises(HTTPException) as context:
            asyncio.run(backend.api_import_project(upload))
        self.assertEqual(context.exception.status_code, 400)
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "outside.txt")))

    def test_clipping_detection(self):
        # Generate normal sine wave
        t = np.linspace(0, 1, 16000)
        y_normal = 0.5 * np.sin(2 * np.pi * 440 * t)
        is_clipped, ratio = backend.check_clipping(y_normal)
        self.assertFalse(is_clipped)
        self.assertEqual(ratio, 0.0)
        
        # Generate clipped wave (artificially clamp values)
        y_clipped = np.sin(2 * np.pi * 440 * t)
        y_clipped[y_clipped > 0.9] = 0.99
        y_clipped[y_clipped < -0.9] = -0.99
        is_clipped, ratio = backend.check_clipping(y_clipped)
        self.assertTrue(is_clipped)
        self.assertTrue(ratio > 0.05) # a substantial portion is clipped

    def test_volume_detection(self):
        sr = 16000
        t = np.linspace(0, 1, sr)
        
        # Too quiet sine wave (RMS is extremely low)
        y_quiet = 0.001 * np.sin(2 * np.pi * 440 * t)
        vol_status, db = backend.check_volume(y_quiet, sr)
        self.assertEqual(vol_status, "too_quiet")
        
        # Normal sine wave (approx -6 dBFS)
        y_normal = 0.5 * np.sin(2 * np.pi * 440 * t)
        vol_status, db = backend.check_volume(y_normal, sr)
        self.assertEqual(vol_status, "normal")
        self.assertTrue(-12 < db < -3)
        
        # Too loud wave (approx 0 dBFS)
        y_loud = 0.98 * np.sin(2 * np.pi * 440 * t)
        vol_status, db = backend.check_volume(y_loud, sr)
        # Note: Depending on threshold, 0.98 sine might be normal or loud
        # Let's make sure it's consistent. Sine RMS is A/sqrt(2), so 0.98 / 1.414 = ~0.69 (-3.2dB)
        # To make it truly too loud (> -3dB), we can use a square wave at 1.0 amplitude
        y_square = np.ones(sr)
        vol_status, db = backend.check_volume(y_square, sr)
        self.assertEqual(vol_status, "too_loud")

    def test_creaky_voice_normal(self):
        # A normal pitch sine wave (440Hz) should NOT be flagged as creaky
        sr = 16000
        t = np.linspace(0, 1, sr)
        y = 0.5 * np.sin(2 * np.pi * 150 * t) # 150 Hz normal speech pitch
        is_creaky, ratio = backend.detect_creak(y, sr)
        self.assertFalse(is_creaky)

    def test_txt_wordlist_parser(self):
        # Mock UploadFile containing plain text wordlist
        content = "【单字阴平】\n妈 衣 书\n【单字阳平】\n麻 移 熟"
        file_obj = UploadFile(filename="test.txt", file=io.BytesIO(content.encode("utf-8")))
        
        # We need to run it asynchronously or call an inner parser
        # Since it's a test, let's run the backend import logic by mocking the bytes read
        import asyncio
        async def run_import():
            return await backend.api_import_wordlist(file_obj)
            
        result = asyncio.run(run_import())
        self.assertEqual(result["status"], "success")
        groups = result["groups"]
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["name"], "单字阴平")
        self.assertEqual(len(groups[0]["items"]), 3)
        self.assertEqual(groups[0]["items"][0]["label"], "妈")
        self.assertEqual(groups[1]["name"], "单字阳平")
        self.assertEqual(len(groups[1]["items"]), 3)

    def test_csv_wordlist_parser(self):
        # Mock CSV contents
        csv_content = (
            "组名,组备注,组标签,词项,词项备注,标签,别名,复核状态\n"
            "单字阴平,单字高平,单字,妈,阴平基准,目标词,mā,复核\n"
            "单字阴平,单字高平,单字,衣,阴平基准,目标词,yī,复核\n"
            "单字阳平,单字中升,单字,麻,阳平基准,目标词,má,复核"
        )
        file_obj = UploadFile(filename="test.csv", file=io.BytesIO(csv_content.encode("utf-8-sig")))
        
        import asyncio
        async def run_import():
            return await backend.api_import_wordlist(file_obj)
            
        result = asyncio.run(run_import())
        self.assertEqual(result["status"], "success")
        groups = result["groups"]
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["name"], "单字阴平")
        self.assertEqual(len(groups[0]["items"]), 2)
        self.assertEqual(groups[0]["items"][0]["label"], "妈")
        self.assertEqual(groups[0]["items"][0]["meta"]["复核状态"], "复核")
        self.assertEqual(groups[1]["name"], "单字阳平")

    def test_ptwl_wordlist_parser(self):
        ptwl_data = {
            "schema": "phontracer.wordlist.v2",
            "title": "测试字表",
            "groups": [
                {
                    "id": "g1",
                    "name": "单字阴平",
                    "note": "阴平组",
                    "items": [
                        {
                            "id": "i1",
                            "label": "妈",
                            "note": "备注",
                            "tags": ["单字"]
                        }
                    ]
                }
            ]
        }
        file_obj = UploadFile(filename="test.ptwl", file=io.BytesIO(json.dumps(ptwl_data).encode("utf-8")))
        
        import asyncio
        async def run_import():
            return await backend.api_import_wordlist(file_obj)
            
        result = asyncio.run(run_import())
        self.assertEqual(result["status"], "success")
        groups = result["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["name"], "单字阴平")
        self.assertEqual(groups[0]["items"][0]["label"], "妈")

if __name__ == '__main__':
    unittest.main()
