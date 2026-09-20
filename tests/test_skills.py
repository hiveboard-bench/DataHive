from typer.testing import CliRunner

from datahive.cli import app
from datahive.skill_install import bundled_skills, install_skills


def test_bundled_skills_present():
    assert {p.name for p in bundled_skills()} == {"datahive-data-prep", "datahive-auto-collect"}


def test_install_skips_then_forces(tmp_path):
    installed, skipped = install_skills(tmp_path)
    assert len(installed) == 2 and not skipped
    assert (tmp_path / "datahive-data-prep" / "SKILL.md").is_file()
    installed, skipped = install_skills(tmp_path)
    assert not installed and len(skipped) == 2
    installed, _ = install_skills(tmp_path, force=True)
    assert len(installed) == 2


def test_cli_install_skill(tmp_path):
    r = CliRunner().invoke(app, ["install-skill", "--dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "datahive-auto-collect" / "reference" / "protocol.md").is_file()
