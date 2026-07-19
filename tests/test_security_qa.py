import unittest
import ast
from multicoders.qa import SecurityScanner

class TestSecurityScanner(unittest.TestCase):
    def setUp(self):
        self.scanner = SecurityScanner()

    def test_detect_eval(self):
        code = "eval('print(123)')"
        tree = ast.parse(code)
        self.scanner.visit(tree)
        self.assertIn("Dangerous built-in call: eval", self.scanner.dangerous_calls)

    def test_detect_os_system(self):
        code = "import os\nos.system('rm -rf /')"
        tree = ast.parse(code)
        self.scanner.visit(tree)
        self.assertIn("Dangerous call: os.system", self.scanner.dangerous_calls)

    def test_detect_subprocess_shell_true(self):
        code = "import subprocess\nsubprocess.Popen('ls', shell=True)"
        tree = ast.parse(code)
        self.scanner.visit(tree)
        self.assertIn("Dangerous call: subprocess.Popen(shell=True)", self.scanner.dangerous_calls)

    def test_safe_code(self):
        code = "def add(a, b):\n    return a + b"
        tree = ast.parse(code)
        self.scanner.visit(tree)
        self.assertEqual(len(self.scanner.dangerous_calls), 0)

if __name__ == "__main__":
    unittest.main()
