"""Check real RTL with the pinned SystemVerilog frontend."""

import json
import pathlib
import tempfile
import unittest

import probe


ROOT = pathlib.Path(__file__).resolve().parents[2]
NOISE_DESIGNS = (
    "3_5_1_synth_generators",
    "3_5_2_synth_channel",
    "3_5_3_synth_music",
    "3_5_4_synth_modulation_am",
    "3_5_5_synth_modulation_fm",
)


class RtlCorpusTests(unittest.TestCase):
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
