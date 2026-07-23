import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONSOLE = ROOT / "static" / "platform.html"


class IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])


class Phase23UITests(unittest.TestCase):
    def setUp(self):
        self.html = CONSOLE.read_text(encoding="utf-8")

    def test_unified_workspaces_and_accessibility_hooks_exist(self):
        for identifier in (
            "detections",
            "routes",
            "forecasts",
            "detectionRows",
            "routeRows",
            "forecastRows",
            "globalSearch",
            "detailPanel",
        ):
            self.assertIn(f'id="{identifier}"', self.html)

    def test_command_center_endpoints_are_wired(self):
        for endpoint in (
            "/api/platform/command-center/overview",
            "/api/platform/command-center/search",
            "/api/platform/command-center/assignments",
            "/api/platform/command-center/comments",
            "/api/platform/autonomous-forecasts/candidates",
            "/api/platform/routes/plans",
            "/api/platform/detections",
        ):
            self.assertIn(endpoint, self.html)

    def test_ids_are_unique(self):
        parser = IdCollector()
        parser.feed(self.html)
        duplicates = sorted(
            identifier
            for identifier in set(parser.ids)
            if parser.ids.count(identifier) > 1
        )
        self.assertEqual(duplicates, [])

    def test_console_javascript_parses_when_node_is_available(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        script = self.html.rsplit("<script>", 1)[1].split("</script>", 1)[0]
        with tempfile.NamedTemporaryFile(
            "w", suffix=".js", encoding="utf-8", delete=False
        ) as handle:
            handle.write(script)
            script_path = Path(handle.name)
        try:
            result = subprocess.run(
                [node, "--check", str(script_path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            script_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
