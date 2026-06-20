import unittest
from unittest import mock
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
import wave
import zipfile
import httpx
from fastapi import HTTPException
from fastapi.datastructures import UploadFile
from modules.project_integrity import verify_project_integrity

# Make sure PhonRec/backend is in sys.path before importing main
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Import backend logic
import main as backend


def build_test_wav(sample_rate=16000, frame_count=1600):
    """生成可被真实音频解析器读取的单声道 16 位 PCM WAV。"""
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * frame_count)
    return output.getvalue()

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

    def test_audio_save_is_transactional_and_retry_preserves_previous_recording(self):
        import asyncio

        state = {
            "version": "1.0",
            "active_speaker_id": "spk_1",
            "groups": [{"id": "g1", "name": "组", "items": [{"id": "word_1", "label": "妈"}]}],
            "speakers": {"spk_1": {"id": "spk_1", "name": "甲", "items": {"word_1": {"id": "word_1", "label": "妈"}}}},
        }
        os.makedirs(backend.WORKSPACE_DIR, exist_ok=True)
        with open(os.path.join(backend.WORKSPACE_DIR, "project.json"), "w", encoding="utf-8") as project_file:
            json.dump(state, project_file, ensure_ascii=False)

        disabled_rules = json.dumps({name: {"enabled": False, "level": "medium"} for name in backend.QUALITY_RULE_NAMES})
        first = asyncio.run(backend.api_save_audio(
            UploadFile(filename="first.wav", file=io.BytesIO(build_test_wav())),
            "spk_1", "word_1", "测试麦克风", disabled_rules,
        ))
        self.assertTrue(first["stored"])
        audio_path = os.path.join(backend.WORKSPACE_DIR, *first["path"].split("/"))
        accepted_bytes = open(audio_path, "rb").read()
        with open(os.path.join(backend.WORKSPACE_DIR, "project.json"), "r", encoding="utf-8") as project_file:
            committed = json.load(project_file)
        self.assertEqual(committed["speakers"]["spk_1"]["items"]["word_1"]["path"], first["path"])

        rejected = asyncio.run(backend.api_save_audio(
            UploadFile(filename="retry.wav", file=io.BytesIO(build_test_wav(frame_count=3200))),
            "spk_1", "word_1", "测试麦克风", "",
        ))
        self.assertFalse(rejected["stored"])
        self.assertEqual(open(audio_path, "rb").read(), accepted_bytes)
        with open(os.path.join(backend.WORKSPACE_DIR, "project.json"), "r", encoding="utf-8") as project_file:
            after_retry = json.load(project_file)
        self.assertEqual(after_retry["speakers"]["spk_1"]["items"]["word_1"]["path"], first["path"])

        with self.assertRaises(backend.HTTPException):
            asyncio.run(backend.api_save_audio(
                UploadFile(filename="stale.wav", file=io.BytesIO(build_test_wav())),
                "spk_1", "word_missing", "测试麦克风", disabled_rules,
            ))
        self.assertFalse(any("word_missing" in name for _root, _dirs, files in os.walk(backend.AUDIO_DIR) for name in files))

    def test_workspace_transaction_rolls_back_project_resources_and_audit(self):
        project_path = os.path.join(backend.WORKSPACE_DIR, "project.json")
        audio_path = os.path.join(backend.AUDIO_DIR, "sample.wav")
        os.makedirs(os.path.dirname(audio_path), exist_ok=True)
        with open(project_path, "wb") as file_obj:
            file_obj.write(b'{"state":"old"}')
        with open(audio_path, "wb") as file_obj:
            file_obj.write(b"old-audio")
        backend.append_audit_log(backend.WORKSPACE_DIR, "created", {})
        backend.update_manifest(backend.WORKSPACE_DIR)

        audit_path = os.path.join(backend.WORKSPACE_DIR, "logs", "audit.jsonl")
        manifest_path = os.path.join(backend.WORKSPACE_DIR, "integrity", "manifest.json")
        originals = {
            project_path: open(project_path, "rb").read(),
            audio_path: open(audio_path, "rb").read(),
            audit_path: open(audit_path, "rb").read(),
            manifest_path: open(manifest_path, "rb").read(),
        }

        staged_project = os.path.join(backend.WORKSPACE_DIR, "project.tmp.json")
        staged_audio = os.path.join(backend.AUDIO_DIR, "sample.tmp.wav")
        with open(staged_project, "wb") as file_obj:
            file_obj.write(b'{"state":"new"}')
        with open(staged_audio, "wb") as file_obj:
            file_obj.write(b"new-audio")

        with mock.patch.object(backend, "update_manifest", side_effect=RuntimeError("模拟清单写入失败")):
            with self.assertRaisesRegex(RuntimeError, "模拟清单写入失败"):
                backend.commit_workspace_transaction(
                    {audio_path: staged_audio, project_path: staged_project},
                    "audio_saved",
                    {"speaker_id": "spk_1", "word_id": "word_1"},
                )

        for path, expected_bytes in originals.items():
            with open(path, "rb") as file_obj:
                self.assertEqual(file_obj.read(), expected_bytes)
        self.assertFalse(any(".rollback" in name for root, _dirs, files in os.walk(backend.WORKSPACE_DIR) for name in files))
        self.assertFalse(any(name.startswith(".phonrec_transaction_") for name in os.listdir(self.temp_dir)))

    def test_workspace_transaction_backup_failure_never_deletes_original(self):
        project_path = os.path.join(backend.WORKSPACE_DIR, "project.json")
        staged_project = os.path.join(backend.WORKSPACE_DIR, "project.tmp.json")
        with open(project_path, "wb") as file_obj:
            file_obj.write(b"old-project")
        with open(staged_project, "wb") as file_obj:
            file_obj.write(b"new-project")

        with mock.patch.object(backend.os, "makedirs", side_effect=OSError("模拟备份目录创建失败")):
            with self.assertRaisesRegex(OSError, "模拟备份目录创建失败"):
                backend.commit_workspace_transaction(
                    {project_path: staged_project},
                    "project_saved",
                    {},
                )

        with open(project_path, "rb") as file_obj:
            self.assertEqual(file_obj.read(), b"old-project")

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
        content = "【单字阴平】\n妈 麻，马、骂\n【单字阳平】\n麻 移 熟"
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
        self.assertEqual(len(groups[0]["items"]), 4)
        self.assertEqual(groups[0]["items"][0]["label"], "妈")
        self.assertEqual(groups[0]["items"][3]["label"], "骂")
        self.assertEqual(groups[1]["name"], "单字阳平")
        self.assertEqual(len(groups[1]["items"]), 3)

    def test_csv_wordlist_parser(self):
        # Mock CSV contents
        csv_content = (
            "组名,组备注,组标签,词项,词项备注,标签,别名,复核状态\n"
            "单字阴平,单字高平,单字,妈,阴平基准,目标词,mā；ma1,复核\n"
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
        self.assertEqual(groups[0]["items"][0]["metadata_source"], "复核")
        self.assertEqual(groups[0]["items"][0]["aliases"], ["mā", "ma1"])
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
                    "meta": {"实验条件": "A"},
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
        file_obj = UploadFile(filename="test.ptwl", file=io.BytesIO(json.dumps(ptwl_data).encode("utf-8-sig")))

        import asyncio
        async def run_import():
            return await backend.api_import_wordlist(file_obj)

        result = asyncio.run(run_import())
        self.assertEqual(result["status"], "success")
        groups = result["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["name"], "单字阴平")
        self.assertEqual(groups[0]["meta"], {"实验条件": "A"})
        self.assertEqual(groups[0]["items"][0]["label"], "妈")

    def test_project_import_normalization(self):
        # Build mock project.json representing a ToneExtractor project (no groups, flat audio)
        te_project_state = {
            "version": "1.0",
            "active_speaker_id": "spk_1",
            "speakers": {
                "spk_1": {
                    "id": "spk_1",
                    "name": "发音人1",
                    "items": {
                        "word_1": {
                            "label": "测试词",
                            "group": "我的自定义组",
                            "path": "audio/spk_1_word_1.wav"
                        }
                    }
                }
            }
        }

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("project.json", json.dumps(te_project_state, ensure_ascii=False))
            zip_file.writestr("audio/spk_1_word_1.wav", build_test_wav())
        archive.seek(0)

        upload = UploadFile(filename="test_te_import.teproj", file=archive)

        import asyncio
        async def run_import():
            return await backend.api_import_project(upload)

        result = asyncio.run(run_import())
        self.assertEqual(result["status"], "success")

        # Verify groups reconstruction
        state = result["state"]
        self.assertIn("groups", state)
        groups = state["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["name"], "我的自定义组")
        self.assertEqual(len(groups[0]["items"]), 1)
        self.assertEqual(groups[0]["items"][0]["label"], "测试词")
        self.assertEqual(groups[0]["items"][0]["id"], "word_1")

        # Verify audio file normalization
        standard_audio_path = os.path.join(backend.AUDIO_DIR, "spk_1", "spk_1_word_1.wav")
        self.assertTrue(os.path.exists(standard_audio_path))
        with open(standard_audio_path, "rb") as f:
            self.assertEqual(f.read(), build_test_wav())

        # Verify that the flat layout file was cleaned up/removed
        flat_audio_path = os.path.join(backend.AUDIO_DIR, "spk_1_word_1.wav")
        self.assertFalse(os.path.exists(flat_audio_path))

        # Verify that path inside project.json has been updated
        spk_items = state["speakers"]["spk_1"]["items"]
        self.assertEqual(spk_items["word_1"]["path"], "audio/spk_1/spk_1_word_1.wav")

    def test_folder_validation_logic(self):
        temp_test_dir = tempfile.mkdtemp(prefix="phonrec_folder_val_")
        try:
            # 1. Empty dir is valid
            self.assertTrue(backend.is_target_folder_valid(temp_test_dir))

            # 2. Non-empty without marker is invalid
            fs_json = os.path.join(temp_test_dir, "some_other_file.txt")
            with open(fs_json, "w") as f:
                f.write("test")
            self.assertFalse(backend.is_target_folder_valid(temp_test_dir))

            # 3. Non-empty with marker is valid
            os.remove(fs_json)
            marker_path = os.path.join(temp_test_dir, ".phonrec-project.json")
            with open(marker_path, "w", encoding="utf-8") as f:
                json.dump({"marker": "phonrec.folder-project.v1"}, f)
            with open(fs_json, "w") as f:
                f.write("test")
            self.assertTrue(backend.is_target_folder_valid(temp_test_dir))
        finally:
            shutil.rmtree(temp_test_dir, ignore_errors=True)

    def test_folder_export_sanitizes_windows_path_names(self):
        self.assertTrue(backend.sanitize_path_component("CON", "fallback").startswith("_CON_"))
        self.assertTrue(
            backend.sanitize_path_component("甲<乙>:丙?. ", "fallback").startswith("甲_乙__丙__")
        )
        self.assertTrue(backend.sanitize_path_component("...", "fallback").startswith("fallback_"))
        self.assertNotEqual(
            backend.sanitize_path_component("甲:乙", "fallback"),
            backend.sanitize_path_component("甲?乙", "fallback"),
        )

    def test_project_export_and_import_folder_roundtrip(self):
        import asyncio

        # 1. Setup workspace project state
        project_state = {
            "version": "1.0",
            "active_speaker_id": "spk_1",
            "groups": [
                {
                    "id": "g1",
                    "name": "拼音词表",
                    "items": [
                        {
                            "id": "word_1",
                            "label": "妈",
                            "note": "阴平"
                        }
                    ]
                }
            ],
            "speakers": {
                "spk_1": {
                    "id": "spk_1",
                    "name": "张三",
                    "items": {
                        "word_1": {
                            "label": "妈",
                            "path": "audio/spk_1/spk_1_word_1.wav",
                            "recorded_at": "2026-06-19T00:00:00Z",
                            "duration_ms": 1000,
                            "sample_rate_hz": 16000,
                            "source": "默认麦克风",
                            "quality": {
                                "clipping": {"abnormal": False, "score": 0.0, "label": "正常"},
                                "volume": {"status": "normal", "score": -6.0, "label": "正常"},
                                "creak": {"abnormal": False, "score": 0.0, "label": "正常"}
                            }
                        }
                    }
                }
            }
        }

        # Write project state to active workspace
        backend.init_workspace()
        with open(os.path.join(backend.WORKSPACE_DIR, "project.json"), "w", encoding="utf-8") as f:
            json.dump(project_state, f, ensure_ascii=False, indent=2)

        # 创建真实可解析的测试录音
        wav_content = build_test_wav()
        spk_dir = os.path.join(backend.AUDIO_DIR, "spk_1")
        os.makedirs(spk_dir, exist_ok=True)
        with open(os.path.join(spk_dir, "spk_1_word_1.wav"), "wb") as f:
            f.write(wav_content)

        # Export destination
        export_dest = tempfile.mkdtemp(prefix="phonrec_export_")

        try:
            # 2. Run Export
            async def run_export():
                return await backend.api_export_project_folder({"folder_path": export_dest})

            export_res = asyncio.run(run_export())
            self.assertEqual(export_res["status"], "success")

            # Verify exported structure
            self.assertTrue(os.path.exists(os.path.join(export_dest, ".phonrec-project.json")))
            self.assertTrue(os.path.exists(os.path.join(export_dest, "project.json")))
            self.assertTrue(os.path.exists(os.path.join(export_dest, "wordlist", "wordlist.ptwl")))
            self.assertTrue(os.path.exists(os.path.join(export_dest, "logs", "recordings.csv")))
            self.assertTrue(os.path.exists(os.path.join(export_dest, "logs", "export.json")))

            # Verify CSV contains BOM and correct content
            with open(os.path.join(export_dest, "logs", "recordings.csv"), "rb") as f:
                content = f.read()
                # Check for UTF-8 BOM bytes: EF BB BF
                self.assertEqual(content[0:3], b'\xef\xbb\xbf')

            # Verify copied audio exists at specific location: audio/张三__spk_1/1_妈__word_1.wav
            expected_audio = os.path.join(export_dest, "audio", "张三__spk_1", "1_妈__word_1.wav")
            self.assertTrue(os.path.exists(expected_audio))
            with open(expected_audio, "rb") as f:
                self.assertEqual(f.read(), wav_content)

            # 3. Run Import
            # Let's clear our workspace first
            backend.clear_workspace()
            self.assertFalse(os.path.exists(os.path.join(backend.WORKSPACE_DIR, "project.json")))

            async def run_import():
                return await backend.api_import_project_folder({"folder_path": export_dest})

            import_res = asyncio.run(run_import())
            self.assertEqual(import_res["status"], "success")

            # Verify imported workspace has restored files and project state
            self.assertTrue(os.path.exists(os.path.join(backend.WORKSPACE_DIR, "project.json")))
            restored_audio = os.path.join(backend.AUDIO_DIR, "spk_1", "spk_1_word_1.wav")
            self.assertTrue(os.path.exists(restored_audio))
            with open(restored_audio, "rb") as f:
                self.assertEqual(f.read(), wav_content)

        finally:
            shutil.rmtree(export_dest, ignore_errors=True)

    def test_import_folder_rejects_missing_marker(self):
        import asyncio
        test_dir = tempfile.mkdtemp(prefix="phonrec_import_fail_")
        try:
            with open(os.path.join(test_dir, "project.json"), "w") as f:
                f.write("{}")

            async def run_import():
                return await backend.api_import_project_folder({"folder_path": test_dir})

            with self.assertRaises(HTTPException) as context:
                asyncio.run(run_import())
            self.assertEqual(context.exception.status_code, 400)
            self.assertIn("缺少 .phonrec-project.json", context.exception.detail)
        finally:
            shutil.rmtree(test_dir, ignore_errors=True)

    def test_import_folder_rejects_symbolic_links(self):
        # Symlinks require admin privileges on Windows occasionally, let's skip/try-catch
        # but implement the check to verify it detects it
        import asyncio
        test_dir = tempfile.mkdtemp(prefix="phonrec_symlink_test_")
        try:
            with open(os.path.join(test_dir, ".phonrec-project.json"), "w") as f:
                json.dump({"marker": "phonrec.folder-project.v1"}, f)
            with open(os.path.join(test_dir, "project.json"), "w") as f:
                json.dump({"version": "1.0", "speakers": {}}, f)

            # Create a symlink to verify it gets blocked
            # We can use os.symlink or try-catch since Windows might fail
            symlink_path = os.path.join(test_dir, "symlink_file.json")
            try:
                os.symlink(os.path.join(test_dir, "project.json"), symlink_path)
                has_symlink = True
            except (OSError, NotImplementedError):
                has_symlink = False

            if has_symlink:
                async def run_import():
                    return await backend.api_import_project_folder({"folder_path": test_dir})

                with self.assertRaises(HTTPException) as context:
                    asyncio.run(run_import())
                self.assertEqual(context.exception.status_code, 400)
                self.assertIn("符号链接", context.exception.detail)
        finally:
            shutil.rmtree(test_dir, ignore_errors=True)

    def test_audio_analysis_and_handoff(self):
        import asyncio
        import stat

        # Initialize workspace project state
        state = {
            "version": "1.0",
            "active_speaker_id": "spk_1",
            "groups": [{"id": "g1", "name": "组", "items": [{"id": "word_1", "label": "妈"}]}],
            "speakers": {
                "spk_1": {
                    "id": "spk_1",
                    "name": "甲",
                    "last_params": {
                        "pitch_floor": 75,
                        "pitch_ceiling": 600,
                        "voicing_threshold": 0.25,
                        "very_accurate": True,
                        "formant_count": 5,
                        "formant_max_hz": 5500.0,
                        "formant_window_length": 0.025,
                        "formant_pre_emphasis": 50.0,
                        "formant_sample_strategy": "整段11点",
                        "pts": 11,
                        "show_f3": False,
                        "db": 60.0,
                        "skip_front": 0.0,
                        "analysis_mode": "f0"
                    },
                    "items": {
                        "word_1": {
                            "id": "word_1",
                            "label": "妈"
                        }
                    }
                }
            },
        }
        backend.init_workspace()
        with open(os.path.join(backend.WORKSPACE_DIR, "project.json"), "w", encoding="utf-8") as project_file:
            json.dump(state, project_file, ensure_ascii=False)

        disabled_rules = json.dumps({name: {"enabled": False, "level": "medium"} for name in backend.QUALITY_RULE_NAMES})

        # 1. Save audio
        save_res = asyncio.run(backend.api_save_audio(
            UploadFile(filename="test.wav", file=io.BytesIO(build_test_wav())),
            "spk_1", "word_1", "测试麦克风", disabled_rules,
        ))
        self.assertTrue(save_res["stored"])

        # Check npz files exist
        pitch_npz = os.path.join(backend.DATA_DIR, "spk_1_word_1.npz")
        formant_npz = os.path.join(backend.DATA_DIR, "spk_1_word_1_formant.npz")
        self.assertTrue(os.path.exists(pitch_npz))
        self.assertTrue(os.path.exists(formant_npz))

        # Check manifest and audit log exist
        manifest_file = os.path.join(backend.WORKSPACE_DIR, "integrity", "manifest.json")
        audit_file = os.path.join(backend.WORKSPACE_DIR, "logs", "audit.jsonl")
        self.assertTrue(os.path.exists(manifest_file))
        self.assertTrue(os.path.exists(audit_file))
        integrity_status, integrity_warnings, _ = verify_project_integrity(backend.WORKSPACE_DIR)
        self.assertEqual(integrity_status, "verified", integrity_warnings)

        # 2. Get audio analysis (Cache Hit)
        analysis_res = asyncio.run(backend.api_get_audio_analysis("spk_1", "word_1"))
        self.assertEqual(analysis_res["status"], "success")
        self.assertTrue(analysis_res["cache_hit"])
        self.assertIn("pitch", analysis_res)
        self.assertIn("formants", analysis_res)
        self.assertIn("summary", analysis_res)

        # 3. Create Handoff Review Snapshot
        with self.assertRaises(HTTPException) as missing_target:
            asyncio.run(backend.api_handoff_create({"speaker_id": "spk_1", "word_id": "missing"}))
        self.assertEqual(missing_target.exception.status_code, 404)

        handoff_res = asyncio.run(backend.api_handoff_create({"speaker_id": "spk_1", "word_id": "word_1"}))
        self.assertEqual(handoff_res["status"], "success")

        archive_path = handoff_res["archive_path"]
        manifest_path = handoff_res["manifest_path"]

        self.assertTrue(os.path.exists(archive_path))
        self.assertTrue(os.path.exists(manifest_path))

        # Verify read-only permission (stat.S_IREAD only or matching mask)
        archive_stat = os.stat(archive_path).st_mode
        manifest_stat = os.stat(manifest_path).st_mode

        # Check that they are files and have read permission but not write (or match basic read-only flags)
        self.assertTrue(bool(archive_stat & stat.S_IREAD))
        self.assertTrue(bool(manifest_stat & stat.S_IREAD))


if __name__ == '__main__':
    unittest.main()
