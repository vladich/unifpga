"""Check real RTL with the pinned SystemVerilog frontend."""

import json
import pathlib
import tempfile
import unittest

import probe
from tools import source_set


ROOT = pathlib.Path(__file__).resolve().parents[2]
NOISE_DESIGNS = (
    "3_5_1_synth_generators",
    "3_5_2_synth_channel",
    "3_5_3_synth_music",
    "3_5_4_synth_modulation_am",
    "3_5_5_synth_modulation_fm",
)


class RtlCorpusTests(unittest.TestCase):
    def test_aps_control_system_elaborates_from_build_fileset(self):
        design_dir = ROOT / "designs" / "5_5_aps"
        sources, _, assets = source_set.design_inputs(str(design_dir))
        source_paths = [pathlib.Path(path).relative_to(ROOT).as_posix()
                        for path in sources]
        source_paths.append("rtl/sim/global_clock_buffer.sv")
        self.assertEqual(len(source_paths), 41)
        self.assertEqual(len(assets), 5)
        request = {
            "schema": probe.SCHEMA_V2,
            "sources": source_paths,
            "include_dirs": [],
            "defines": [],
            "top": "design_top",
            "compilation_unit": "separate",
            "top_parameter_overrides": [
                "clk_mhz=50", "w_btn=4", "w_sw=8", "w_led=8", "w_digit=8",
                "w_gpio=100", "screen_width=640", "screen_height=480",
                "w_red=4", "w_green=4", "w_blue=4",
            ],
        }
        with tempfile.TemporaryDirectory() as scratch:
            request_path = pathlib.Path(scratch) / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            result = probe.run(ROOT, request_path)
        self.assertTrue(result["accepted"], result)
        graph = result["elaboration"]
        self.assertEqual(graph["projection_status"], "complete")
        self.assertEqual(graph["semantic_completeness"], "unproven")
        self.assertEqual(len(graph["instances"]), 44)
        definitions = {instance["definition"] for instance in graph["instances"]}
        self.assertTrue({"processor_core", "rw_instr_mem", "data_mem",
                         "uart_rx_sb_ctrl", "uart_tx_sb_ctrl", "timer_sb_ctrl",
                         "interrupt_controller"} <= definitions)
        top = graph["instances"][0]
        self.assertEqual(top["path"], "design_top")
        self.assertEqual(next(port["evaluated_bit_width"] for port in top["ports"]
                              if port["name"] == "sw"), 8)
        self.assertEqual(next(parameter["evaluated_value"] for parameter in top["parameters"]
                              if parameter["name"] == "screen_width"), "640")

    def test_noise_seed_preserves_its_23_bit_value(self):
        for design in NOISE_DESIGNS:
            source = "designs/{}/audio_noise.sv".format(design)
            with self.subTest(source=source), tempfile.TemporaryDirectory() as scratch:
                request_path = pathlib.Path(scratch) / "request.json"
                request_path.write_text(json.dumps({
                    "schema": probe.SCHEMA,
                    "sources": [source], "include_dirs": [], "defines": [],
                    "top": "audio_noise", "compilation_unit": "separate",
                }), encoding="utf-8")
                result = probe.run(ROOT, request_path)
                self.assertTrue(result["accepted"], result)
                warnings = result["parse_diagnostics"] + result["semantic_diagnostics"]
                self.assertNotIn("DiagCode(VectorLiteralOverflow)",
                                 [warning["code"] for warning in warnings])
                parameters = result["elaboration"]["instances"][0]["parameters"]
                seed = next(row for row in parameters if row["name"] == "NOISE_SHREG_INIT")
                self.assertEqual(seed["type"], "logic[22:0]")
                self.assertEqual(seed["evaluated_value"], "23'd8388600")


if __name__ == "__main__":
    unittest.main()
