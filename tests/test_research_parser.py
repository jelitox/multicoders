import unittest
from pathlib import Path

from multicoders.parrot_lens import ParrotLens
from multicoders.research import ResearchNode
from multicoders.storage import Storage


class TestParrotLens(unittest.TestCase):
    def test_api_surface_handles_missing_path(self):
        # Signature extraction now lives in ParrotLens. A non-existent path must
        # not raise and must report that nothing real could be extracted.
        lens = ParrotLens(parrot_path=Path("/tmp/nonexistent_parrot"))
        surface = lens.get_api_surface()
        self.assertIn("path not found", surface)

    def test_api_surface_extracts_real_signatures(self):
        # Against the vendored ai-parrot sources the lens should surface classes.
        lens = ParrotLens()
        if lens.parrot_path is None:
            self.skipTest("vendored ai-parrot sources not available")
        surface = lens.get_api_surface()
        self.assertIn("Parrot API Surface", surface)


class TestResearchEnrich(unittest.TestCase):
    def setUp(self):
        self.storage = Storage(":memory:")
        self.node = ResearchNode(self.storage, repo_root=Path("."))

    def test_enrich_smoke(self):
        ctx = self.node.enrich("test_id", "test prompt")
        self.assertIn("Task: test prompt", ctx.enriched_prompt)
        self.assertIn("Repository Files:", ctx.enriched_prompt)


if __name__ == "__main__":
    unittest.main()
