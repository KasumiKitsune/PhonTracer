import io
import json
import math
import os
import shutil
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout

import numpy as np

from cli import PhonTracerCLI
from modules.wordlist_v2 import (
    WORDLIST_SCHEMA,
    build_document_from_messy_text,
    build_document_from_structured_text,
    flatten_wordlist_document,
    load_wordlist_file,
    load_wordlist_from_csv_file,
    looks_like_structured_wordlist_text,
    save_wordlist_file,
)


def _write_synth_wav(path: str, freq_hz: float = 200.0, duration_s: float = 0.4, sr: int = 16000):
    import scipy.io.wavfile as wavfile

    n = int(round(duration_s * sr))
    t = np.arange(n, dtype=np.float64) / sr
    sig = 0.5 * np.sin(2.0 * math.pi * freq_hz * t)
    pcm = np.clip(sig * 32767.0, -32768, 32767).astype(np.int16)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    wavfile.write(path, sr, pcm)


def _write_multi_segment_long_wav(path: str, freqs=(220.0, 240.0), seg_dur: float = 0.35, gap_dur: float = 0.25, sr: int = 16000):
    import scipy.io.wavfile as wavfile

    parts = []
    silence = np.zeros(int(round(gap_dur * sr)), dtype=np.float64)
    parts.append(silence)
    for f in freqs:
        n = int(round(seg_dur * sr))
        t = np.arange(n, dtype=np.float64) / sr
        parts.append(0.6 * np.sin(2.0 * math.pi * float(f) * t))
        parts.append(silence)
    sig = np.concatenate(parts)
    pcm = np.clip(sig * 32767.0, -32768, 32767).astype(np.int16)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    wavfile.write(path, sr, pcm)


class TestModuleCAgent2(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_module_c_")
        self.cli = PhonTracerCLI()

    def tearDown(self):
        self.cli.cleanup_workspace()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _run_cmd(self, cmd_line: str) -> dict:
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.cli.onecmd(cmd_line)
        lines = [line.strip() for line in buf.getvalue().splitlines() if line.strip()]
        self.assertTrue(len(lines) >= 1, f"No JSON output from command: {cmd_line}")
        return json.loads(lines[-1])

    def test_1_ptwl_csv_structured_and_messy_txt_import_and_item_mounting(self):
        """测试 P0 高级字表 (.ptwl / .csv / 结构化 .txt / 杂乱 .txt) 导入与字段挂载（batch 与 single/long 模式）。"""
        # Prepare batch audio files
        batch_dir = os.path.join(self.tmp_dir, "batch_audios")
        w1 = os.path.join(batch_dir, "01_播音.wav")
        w2 = os.path.join(batch_dir, "02_高峰.wav")
        _write_synth_wav(w1, freq_hz=210.0)
        _write_synth_wav(w2, freq_hz=230.0)

        # 1) .ptwl import in batch mode
        ptwl_in = os.path.join(self.tmp_dir, "input.ptwl")
        save_wordlist_file(
            {
                "schema": WORDLIST_SCHEMA,
                "title": "普通话声调测试字表",
                "groups": [
                    {
                        "name": "阴阴组",
                        "note": "阴平+阴平",
                        "tags": ["主测试"],
                        "meta": {"组属性": "双音节"},
                        "items": [
                            {
                                "label": "播音",
                                "note": "播音备注",
                                "tags": ["目标词", "阴阴"],
                                "aliases": ["boyin"],
                                "meta": {"结构": "动宾", "拼音": "bo1 yin1"},
                            },
                            {
                                "label": "高峰",
                                "note": "高峰备注",
                                "tags": ["目标词"],
                                "aliases": ["gaofeng"],
                                "meta": {"结构": "偏正", "拼音": "gao1 feng1"},
                            },
                        ],
                    }
                ],
            },
            ptwl_in,
        )

        res_load = self._run_cmd(f'load_batch "{batch_dir}"')
        self.assertTrue(res_load["success"])
        self.assertEqual(res_load["count"], 2)

        res_wl = self._run_cmd(f'apply_wordlist "{ptwl_in}" fuzzy')
        self.assertTrue(res_wl["success"])
        self.assertEqual(res_wl["total"], 2)
        self.assertIsNotNone(self.cli.wordlist_doc)
        self.assertEqual(self.cli.wordlist_doc["schema"], WORDLIST_SCHEMA)
        self.assertEqual(self.cli.current_speaker.wordlist_doc["title"], "普通话声调测试字表")

        it0 = self.cli.items["item_0"]
        self.assertEqual(it0["group"], "阴阴组")
        self.assertEqual(it0["label"], "播音")
        self.assertIn("目标词", it0["tags"])
        self.assertEqual(it0["note"], "播音备注")
        self.assertEqual(it0["fields"]["结构"], "动宾")
        self.assertEqual(it0["custom_fields"]["拼音"], "bo1 yin1")
        self.assertEqual(it0["custom_fields"]["组属性"], "双音节")

        # 2) .csv import
        csv_in = os.path.join(self.tmp_dir, "input.csv")
        with open(csv_in, "w", encoding="utf-8") as f:
            f.write("组名,词项,词项备注,标签,实验条件,结构\n")
            f.write("CSV实验组,播音,CSV备注1,目标词；阴平,快读,动宾\n")
            f.write("CSV实验组,高峰,CSV备注2,填充词,慢读,偏正\n")

        res_csv = self._run_cmd(f'apply_wordlist "{csv_in}" order')
        self.assertTrue(res_csv["success"])
        it_csv = self.cli.items["item_0"]
        self.assertEqual(it_csv["group"], "CSV实验组")
        self.assertEqual(it_csv["label"], "播音")
        self.assertEqual(it_csv["note"], "CSV备注1")
        self.assertEqual(it_csv["tags"], ["目标词", "阴平"])
        self.assertEqual(it_csv["fields"]["实验条件"], "快读")
        self.assertEqual(it_csv["custom_fields"]["结构"], "动宾")

        # 3) Structured .txt import in single/long audio mode
        long_wav = os.path.join(self.tmp_dir, "long_speech.wav")
        _write_multi_segment_long_wav(long_wav, freqs=(210.0, 250.0))
        struct_txt = os.path.join(self.tmp_dir, "structured.txt")
        struct_content = (
            "title: 结构化长音频字表\n"
            "@group 结构组A | tags=组标签1 | note=组备注A\n"
            "@defaults | tags=默认标签 | 语速=正常\n"
            "播音 | tags=目标词；重点 | note=第一词 | 结构=动宾\n"
            "高峰 | note=第二词 | 结构=偏正\n"
        )
        with open(struct_txt, "w", encoding="utf-8") as f:
            f.write(struct_content)

        self.assertTrue(looks_like_structured_wordlist_text(struct_content))
        res_long = self._run_cmd(f'load_long "{long_wav}"')
        self.assertTrue(res_long["success"])
        # Verify mode == 'single' compatibility
        self.cli.mode = "single"
        res_struct = self._run_cmd(f'apply_wordlist "{struct_txt}"')
        self.assertTrue(res_struct["success"])
        self.assertEqual(self.cli.wordlist_doc["title"], "结构化长音频字表")
        it_s0 = self.cli.items["item_0"]
        self.assertEqual(it_s0["group"], "结构组A")
        self.assertEqual(it_s0["label"], "播音")
        self.assertEqual(it_s0["tags"], ["目标词", "重点"])
        self.assertEqual(it_s0["note"], "第一词")
        self.assertEqual(it_s0["fields"]["结构"], "动宾")
        self.assertEqual(it_s0["custom_fields"]["语速"], "正常")

        # 4) Messy .txt import
        messy_txt = os.path.join(self.tmp_dir, "messy.txt")
        with open(messy_txt, "w", encoding="utf-8") as f:
            f.write("【杂乱第一组】 1.播音(boyin) 2、高峰（阴阴目标词）\n")
        res_messy = self._run_cmd(f'apply_wordlist "{messy_txt}"')
        self.assertTrue(res_messy["success"])
        self.assertEqual(self.cli.items["item_0"]["group"], "杂乱第一组")
        self.assertEqual(self.cli.items["item_0"]["label"], "播音")
        self.assertIn("boyin", self.cli.items["item_0"]["aliases"])
        self.assertEqual(self.cli.items["item_1"]["label"], "高峰")
        self.assertIn("阴阴目标词", self.cli.items["item_1"]["note"])

    def test_2_export_ptwl_and_tool_wordlist_convert(self):
        """测试 export ptwl（含仅 apply_wordlist 无音频状态）与 tool_wordlist_convert 独立命令。"""
        # 1) tool_wordlist_convert from messy text
        messy_in = os.path.join(self.tmp_dir, "raw_messy.txt")
        with open(messy_in, "w", encoding="utf-8") as f:
            f.write(
                "【阴平组】\n"
                "1. 播音(boyin)  2. 高峰\n"
                "阳平组：资源、发明\n"
            )
        ptwl_out = os.path.join(self.tmp_dir, "converted.ptwl")
        res_conv = self._run_cmd(f'tool_wordlist_convert "{messy_in}" "{ptwl_out}" title="升级后的高级字表"')
        self.assertTrue(res_conv["success"])
        self.assertEqual(os.path.normpath(res_conv["output_file"]), os.path.normpath(ptwl_out))
        self.assertEqual(res_conv["title"], "升级后的高级字表")
        self.assertEqual(res_conv["group_count"], 2)
        self.assertEqual(res_conv["item_count"], 4)
        self.assertTrue(os.path.exists(ptwl_out))

        loaded_doc = load_wordlist_file(ptwl_out)
        self.assertEqual(loaded_doc["schema"], WORDLIST_SCHEMA)
        self.assertEqual(loaded_doc["title"], "升级后的高级字表")

        # 2) export ptwl when ONLY apply_wordlist was run on a fresh CLI (no audio loaded yet)
        fresh_cli = PhonTracerCLI()
        try:
            struct_in = os.path.join(self.tmp_dir, "only_wl.txt")
            with open(struct_in, "w", encoding="utf-8") as f:
                f.write(
                    "title: 纯字表加载导出测试\n"
                    "@group 测试组 | tags=组T\n"
                    "播音 | tags=词T | note=备注A | 方言点=北京\n"
                )
            buf = io.StringIO()
            with redirect_stdout(buf):
                fresh_cli.onecmd(f'apply_wordlist "{struct_in}"')
            out_ptwl_2 = os.path.join(self.tmp_dir, "exported_from_wl_only.ptwl")
            buf2 = io.StringIO()
            with redirect_stdout(buf2):
                fresh_cli.onecmd(f'export ptwl "{out_ptwl_2}"')
            res_exp = json.loads(buf2.getvalue().strip().splitlines()[-1])
            self.assertTrue(res_exp["success"])
            self.assertTrue(os.path.exists(out_ptwl_2))
            exp_doc = load_wordlist_file(out_ptwl_2)
            self.assertEqual(exp_doc["schema"], WORDLIST_SCHEMA)
            self.assertEqual(exp_doc["title"], "纯字表加载导出测试")
            self.assertEqual(exp_doc["groups"][0]["items"][0]["label"], "播音")
            self.assertEqual(exp_doc["groups"][0]["items"][0]["meta"]["方言点"], "北京")
        finally:
            fresh_cli.cleanup_workspace()

    def test_3_load_batch_dir_glob_hint_and_tool_sort_batch_apply(self):
        """测试 load_batch 目录扫描、通配符展开、自然排序、错误 hint 以及 tool_sort_batch --apply。"""
        iso_dir = os.path.join(self.tmp_dir, "female_isolated")
        # Create 10_c.wav, 2_b.wav, 1_a.wav to verify natural sort order (1, 2, 10)
        _write_synth_wav(os.path.join(iso_dir, "10_测试十.wav"), 240.0)
        _write_synth_wav(os.path.join(iso_dir, "2_测试二.wav"), 220.0)
        _write_synth_wav(os.path.join(iso_dir, "1_测试一.wav"), 200.0)

        # 1) Directory path load_batch
        res_dir = self._run_cmd(f'load_batch "{iso_dir}"')
        self.assertTrue(res_dir["success"])
        self.assertEqual(res_dir["count"], 3)
        basenames = [os.path.basename(p) for p in self.cli.batch_paths]
        self.assertEqual(basenames, ["1_测试一.wav", "2_测试二.wav", "10_测试十.wav"])

        # 2) Wildcard pattern load_batch
        glob_pattern = os.path.join(iso_dir, "*.wav")
        res_glob = self._run_cmd(f'load_batch "{glob_pattern}"')
        self.assertTrue(res_glob["success"])
        self.assertEqual(res_glob["count"], 3)
        self.assertEqual([os.path.basename(p) for p in self.cli.batch_paths], ["1_测试一.wav", "2_测试二.wav", "10_测试十.wav"])

        # 3) Missing wildcard / empty directory hint check
        bad_glob = os.path.join(iso_dir, "nonexistent_*.wav")
        res_err = self._run_cmd(f'load_batch "{bad_glob}"')
        self.assertFalse(res_err["success"])
        self.assertIn("hint", res_err)
        self.assertIn("*.wav", res_err["hint"])

        # 4) tool_sort_batch with directory/glob and --apply
        wl_sort = os.path.join(self.tmp_dir, "sort_wl.txt")
        with open(wl_sort, "w", encoding="utf-8") as f:
            f.write("测试十\n测试二\n测试一\n")
        res_sort = self._run_cmd(f'tool_sort_batch "{wl_sort}" "{iso_dir}" --apply')
        self.assertTrue(res_sort["success"])
        self.assertTrue(res_sort["applied"])
        self.assertEqual(
            [os.path.basename(p) for p in self.cli.batch_paths],
            ["10_测试十.wav", "2_测试二.wav", "1_测试一.wav"],
        )

    def test_4_import_batch_and_export_with_wordlist(self):
        """测试 import_batch_and_export 支持 wordlist=<path> 与 match_mode=fuzzy|order 及默认覆盖空壳发音人。"""
        multi_root = os.path.join(self.tmp_dir, "multi_spk_batch")
        spk_f_dir = os.path.join(multi_root, "女声组")
        spk_m_dir = os.path.join(multi_root, "男声组")
        _write_synth_wav(os.path.join(spk_f_dir, "01_播音.wav"), 240.0)
        _write_synth_wav(os.path.join(spk_f_dir, "02_高峰.wav"), 260.0)
        _write_synth_wav(os.path.join(spk_m_dir, "01_播音.wav"), 120.0)
        _write_synth_wav(os.path.join(spk_m_dir, "02_高峰.wav"), 140.0)

        wl_ptwl = os.path.join(self.tmp_dir, "batch_wl.ptwl")
        save_wordlist_file(
            {
                "schema": WORDLIST_SCHEMA,
                "title": "批量工程字表",
                "groups": [
                    {
                        "name": "11_阴阴",
                        "tags": ["主测试"],
                        "items": [
                            {"label": "播音", "tags": ["目标词"], "note": "注1", "meta": {"结构": "动宾"}},
                            {"label": "高峰", "tags": ["目标词"], "note": "注2", "meta": {"结构": "偏正"}},
                        ],
                    }
                ],
            },
            wl_ptwl,
        )

        out_teproj = os.path.join(self.tmp_dir, "batch_integrated.teproj")
        res = self._run_cmd(
            f'import_batch_and_export "{multi_root}" "{out_teproj}" wordlist="{wl_ptwl}" match_mode=fuzzy'
        )
        self.assertTrue(res["success"])
        self.assertTrue(os.path.exists(out_teproj))

        all_spks = self.cli.speaker_manager.get_all_speakers()
        spk_names = [s.name for s in all_spks]
        self.assertEqual(spk_names, ["女声组", "男声组"])
        self.assertNotIn("发音人 1", spk_names)

        for s in all_spks:
            self.assertEqual(len(s.items), 2)
            first_item = next(iter(s.items.values()))
            self.assertEqual(first_item["group"], "11_阴阴")
            self.assertEqual(first_item["fields"]["结构"], "动宾")
            self.assertIn("目标词", first_item["tags"])

        with zipfile.ZipFile(out_teproj, "r") as zf:
            proj_data = json.loads(zf.read("project.json").decode("utf-8"))
            self.assertEqual(len(proj_data["speakers"]), 2)
            self.assertEqual(proj_data.get("wordlist_title"), "批量工程字表")

    def test_5_speaker_lifecycle_and_integrated_export_summary(self):
        """测试 add_speaker 自动覆盖初始空壳、rename_speaker 同步更新及 export integrated 的 integrated_summary。"""
        # 1) Initial state has 1 empty default speaker "发音人 1"
        self.assertEqual(len(self.cli.speaker_manager.get_all_speakers()), 1)
        self.assertEqual(self.cli.current_speaker.name, "发音人 1")

        # First add_speaker should replace the empty "发音人 1" shell and auto-switch
        res_add1 = self._run_cmd('add_speaker "女发音人A"')
        self.assertTrue(res_add1["success"])
        self.assertTrue(res_add1["replaced_default"])
        self.assertTrue(res_add1["active"])
        self.assertEqual([s.name for s in self.cli.speaker_manager.get_all_speakers()], ["女发音人A"])

        # Load audio & wordlist for speaker 1
        f_dir = os.path.join(self.tmp_dir, "spk_f")
        _write_synth_wav(os.path.join(f_dir, "01_播音.wav"), 240.0)
        _write_synth_wav(os.path.join(f_dir, "02_高峰.wav"), 280.0)
        wl_path = os.path.join(self.tmp_dir, "simple.txt")
        with open(wl_path, "w", encoding="utf-8") as f:
            f.write("【阴平组】\n播音 高峰\n")

        self._run_cmd(f'load_batch "{f_dir}"')
        self._run_cmd(f'apply_wordlist "{wl_path}" order')

        # Rename active speaker with 1 argument
        res_ren1 = self._run_cmd('rename_speaker "女1"')
        self.assertTrue(res_ren1["success"])
        self.assertEqual(self.cli.current_speaker.name, "女1")
        for it in self.cli.items.values():
            self.assertEqual(it["speaker"], "女1")
            self.assertEqual(it["speaker_id"], self.cli.current_speaker.id)

        # Add second speaker (should NOT overwrite "女1", and should auto-switch unless --no-switch)
        res_add2 = self._run_cmd('add_speaker "男发音人B" --switch')
        self.assertTrue(res_add2["success"])
        self.assertFalse(res_add2["replaced_default"])
        self.assertEqual(self.cli.current_speaker.name, "男发音人B")
        self.assertEqual([s.name for s in self.cli.speaker_manager.get_all_speakers()], ["女1", "男发音人B"])

        # Rename speaker with 2 arguments (<old_name> <new_name>)
        res_ren2 = self._run_cmd('rename_speaker "男发音人B" "男1"')
        self.assertTrue(res_ren2["success"])
        self.assertEqual(self.cli.current_speaker.name, "男1")

        m_dir = os.path.join(self.tmp_dir, "spk_m")
        _write_synth_wav(os.path.join(m_dir, "01_播音.wav"), 110.0)
        _write_synth_wav(os.path.join(m_dir, "02_高峰.wav"), 150.0)
        self._run_cmd(f'load_batch "{m_dir}"')
        self._run_cmd(f'apply_wordlist "{wl_path}" order')

        # Export integrated xlsx and check integrated_summary
        out_xlsx = os.path.join(self.tmp_dir, "integrated.xlsx")
        res_exp = self._run_cmd(f'export xlsx "{out_xlsx}" continuous integrated')
        self.assertTrue(res_exp["success"])
        self.assertIn("integrated_summary", res_exp)
        summary = res_exp["integrated_summary"]
        self.assertEqual(summary["speakers"], ["女1", "男1"])
        self.assertEqual(summary["speaker_item_counts"], {"女1": 2, "男1": 2})
        self.assertIn("女1", summary["speaker_baselines"])
        self.assertIn("男1", summary["speaker_baselines"])
        self.assertGreater(summary["speaker_baselines"]["女1"]["max_hz"], 0.0)
        self.assertGreater(summary["speaker_baselines"]["男1"]["max_hz"], 0.0)

        # Also test scope=integrated on scientific chart export (contour)
        out_contour = os.path.join(self.tmp_dir, "integrated_contour.png")
        res_contour = self._run_cmd(f'export contour "{out_contour}" scope=integrated')
        self.assertTrue(res_contour["success"])
        self.assertIn("integrated_summary", res_contour)
        self.assertEqual(res_contour["integrated_summary"]["speakers"], ["女1", "男1"])


if __name__ == "__main__":
    unittest.main()
