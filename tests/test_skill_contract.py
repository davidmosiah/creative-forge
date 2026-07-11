import unittest
from pathlib import Path

import yaml


class SkillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent.parent
        cls.skill = cls.root / "skill" / "criar-criativos" / "SKILL.md"
        cls.text = cls.skill.read_text()

    def test_frontmatter_is_trigger_only_and_portable(self):
        _, frontmatter, _ = self.text.split("---", 2)
        data = yaml.safe_load(frontmatter)
        self.assertEqual(set(data), {"name", "description"})
        self.assertTrue(data["description"].startswith("Use when"))
        self.assertIn("Codex", data["description"])
        self.assertIn("Claude", data["description"])

    def test_skill_requires_full_pipeline_and_visual_qa(self):
        for required in (
            "scripts/forge.py preflight",
            "scripts/forge.py build",
            "scripts/qa.py approve",
            "scripts/forge.py prepare-publish",
            "contact sheets",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.text)

    def test_skill_discovers_mcp_capabilities_without_inventing_them(self):
        for provider in ("PostHog MCP", "Meta Ads MCP", "Higgsfield", "Remotion"):
            self.assertIn(provider, self.text)
        self.assertIn("capability receipt", self.text)
        self.assertIn("não invente", self.text)

    def test_skill_forbids_activation_and_spend(self):
        self.assertIn("PAUSED", self.text)
        self.assertIn("nunca ativar", self.text)
        self.assertIn("gasto", self.text)

    def test_installer_targets_both_agent_skill_directories(self):
        installer = (self.root / "scripts" / "install-skill.sh").read_text()
        self.assertIn(".codex/skills/criar-criativos", installer)
        self.assertIn(".claude/skills/criar-criativos", installer)
        self.assertIn("ln -s", installer)


if __name__ == "__main__":
    unittest.main()
