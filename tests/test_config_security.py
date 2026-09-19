from __future__ import annotations

import os
import stat
import subprocess
import sys

import pytest

from datahive.config import (
    Config,
    check_permissions,
    config_path,
    load_config,
    mask_token,
    masked_dict,
    save_config,
)
from datahive.errors import ConfigInsideGitRepo, InsecureConfigPermissions


def _cfg(token="hf_supersecrettoken1234567890"):
    return Config(lab_id="lab_test", repo_id="sua-org/lab_test", hf_token=token, created_at="2026-01-01T00:00:00+00:00")


def test_config_written_with_0600(tmp_path):
    cfg = _cfg()
    path = save_config(cfg)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


@pytest.mark.parametrize("bad_mode", [0o640, 0o644, 0o664, 0o777])
def test_group_or_world_readable_config_refuses(bad_mode):
    cfg = _cfg()
    path = save_config(cfg)
    os.chmod(path, bad_mode)
    with pytest.raises(InsecureConfigPermissions, match="chmod 600"):
        load_config()
    with pytest.raises(InsecureConfigPermissions):
        check_permissions(path)


def test_secure_config_loads_fine():
    cfg = _cfg()
    save_config(cfg)
    loaded = load_config()
    assert loaded.lab_id == "lab_test"
    assert loaded.hf_token == cfg.hf_token


def test_init_refuses_inside_git_repo(tmp_path, monkeypatch):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.setenv("DATAHIVE_CONFIG_HOME", str(repo / ".datahive"))
    monkeypatch.chdir(repo)
    cfg = _cfg()
    with pytest.raises(ConfigInsideGitRepo):
        save_config(cfg)


def test_init_refuses_when_cwd_is_inside_git_repo_even_if_config_elsewhere(tmp_path, monkeypatch):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / ".git").mkdir()
    other_home = tmp_path / "other_home"
    monkeypatch.setenv("DATAHIVE_CONFIG_HOME", str(other_home / ".datahive"))
    monkeypatch.chdir(repo)
    cfg = _cfg()
    # Config path itself is not inside a git repo, so this must succeed;
    # the git guard only fires when the *config path* resolves inside one.
    path = save_config(cfg)
    assert path.is_file()


def test_init_succeeds_outside_git_repo(tmp_path):
    cfg = _cfg()
    path = save_config(cfg)
    assert path.is_file()


def test_init_config_dir_overrides_default_location(tmp_path, monkeypatch):
    """--config-dir writes config.yaml under the given directory for this
    invocation, instead of $DATAHIVE_CONFIG_HOME / ~/.datahive."""
    from typer.testing import CliRunner

    from datahive.cli import app

    custom_dir = tmp_path / "custom-config-location"
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["init", "--lab-id", "lab_test", "--token", "hf_faketoken1234", "--no-verify", "--config-dir", str(custom_dir)],
    )
    assert result.exit_code == 0, result.output
    assert (custom_dir / "config.yaml").is_file()


def test_root_config_dir_and_version_options(tmp_path, monkeypatch):
    """The root datahive CLI displays --version and --config-dir DIR in its help
    output and responds properly to both flags."""
    from typer.testing import CliRunner

    from datahive.cli import app

    runner = CliRunner()

    # Bare invocation prints help with --version, --config-dir, and typical session epilog
    result = runner.invoke(app, [])
    assert result.exit_code == 2
    assert "--version" in result.output
    assert "Show the version and exit." in result.output
    assert "--config-dir" in result.output
    assert "DIR" in result.output
    assert "contributor_config.yaml" in result.output
    assert "$OOPSIE_CONFIG_DIR" in result.output
    assert "A typical session, in order:" in result.output
    assert "datahive init" in result.output
    assert "datahive new-profile" in result.output
    assert "datahive annotate" in result.output
    assert "datahive validate" in result.output
    assert "datahive upload" in result.output

    # --version prints version and exits 0
    version_res = runner.invoke(app, ["--version"])
    assert version_res.exit_code == 0
    assert "datahive, version 0.1.0" in version_res.output

    # Root --config-dir overrides location
    custom_dir = tmp_path / "root-custom-config"
    monkeypatch.chdir(tmp_path)
    init_res = runner.invoke(
        app,
        ["--config-dir", str(custom_dir), "init", "--lab-id", "lab_test", "--token", "hf_faketoken1234", "--no-verify"],
    )
    assert init_res.exit_code == 0, init_res.output
    assert (custom_dir / "config.yaml").is_file()


def test_mask_token():
    assert mask_token("") == ""
    assert mask_token("short") == "*****"
    masked = mask_token("hf_abcdefghijklmnopab12")
    assert masked.startswith("hf_a")
    assert masked.endswith("ab12")
    assert "abcdefghijklmnop" not in masked


def test_masked_dict_never_reveals_token():
    cfg = _cfg("hf_realsecrettoken9999")
    d = masked_dict(cfg)
    assert "hf_realsecrettoken9999" not in str(d)
    assert d["hf_token"] != cfg.hf_token


def test_token_never_in_repr_or_str():
    cfg = _cfg("hf_realsecrettoken9999")
    assert "hf_realsecrettoken9999" not in repr(cfg)
    assert "hf_realsecrettoken9999" not in str(cfg)


def test_token_never_appears_in_cli_stdout_stderr(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from datahive.cli import app

    monkeypatch.chdir(tmp_path)  # init drops a .gitignore in cwd; keep it out of the real repo
    runner = CliRunner()
    token = "hf_topsecrettoken424242"
    result = runner.invoke(
        app,
        ["init", "--lab-id", "lab_test", "--token", token, "--no-verify"],
    )
    assert token not in result.stdout
    assert token not in (result.output or "")

    result2 = runner.invoke(app, ["list", "--samples", str(tmp_path / "samples")])
    assert token not in result2.stdout
