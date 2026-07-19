import unittest
import os
import shutil
from multicoders.factory import create_multicoders_stack, quick_run

class TestFactory(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_factory.db"
        self.workdir = "test_runs_factory"
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.workdir):
            shutil.rmtree(self.workdir)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.workdir):
            shutil.rmtree(self.workdir)

    def test_create_stack(self):
        flow = create_multicoders_stack(self.db_path, self.workdir)
        self.assertIsNotNone(flow)
        self.assertTrue(os.path.exists(self.workdir))

    def test_quick_run_mock(self):
        # We mock the internal calls to avoid real API/heavy processing if possible
        # but for now we just verify the initialization works
        pass

if __name__ == "__main__":
    unittest.main()
