"""`datahive` CLI. Thin: parses arguments, calls the shared library/ops
functions, formats output. No business logic lives here."""

from __future__ import annotations

import json as jsonlib
import sys
from pathlib import Path
from typing import Optional

import typer

from datahive import ops
from datahive.config import (
    Config,
    check_permissions,
    config_path,
    load_config,
    masked_dict,
    maybe_write_gitignore,
    now_iso,
    save_config,
)
from datahive.errors import DatahiveError
from datahive.hub import Hub
from datahive.index import Index
from datahive.paths import default_samples_root
from datahive.profile import write_profile_skeleton
from datahive.schema import FailureCause, Outcome, Strategy

app = typer.Typer(add_completion=False, no_args_is_help=True, help="datahive-tools client CLI")


def _samples_opt(samples: Optional[str]) -> Path:
    return Path(samples).resolve() if samples else default_samples_root()


SAMPLES_OPTION = typer.Option(None, "--samples", help="Path to the samples/ directory (default: ./samples)")


@app.command()
def init(
    lab_id: str = typer.Option(..., prompt=True),
    repo_id: Optional[str] = typer.Option(None, help="Defaults to sua-org/{lab_id}"),
    platform_id: str = typer.Option(..., prompt=True),
    token: str = typer.Option(..., prompt=True, hide_input=True, help="Hugging Face token"),
    endpoint: Optional[str] = typer.Option(None, help="Custom HF endpoint (advanced)"),
    no_verify: bool = typer.Option(False, "--no-verify", help="Skip the whoami() check"),
    force: bool = typer.Option(False, "--force"),
):
    """One-time setup: writes ~/.datahive/config.yaml (0600, never inside a git repo)."""
    target = config_path()
    if target.exists() and not force:
        typer.echo(f"Config already exists at {target}. Pass --force to overwrite.", err=True)
        raise typer.Exit(2)

    repo_id = repo_id or f"sua-org/{lab_id}"
    cfg = Config(
        lab_id=lab_id, repo_id=repo_id, platform_id=platform_id, hf_token=token,
        endpoint=endpoint, created_at=now_iso(),
    )

    if not no_verify:
        try:
            Hub(cfg).whoami()
            typer.echo("Token verified with the Hugging Face Hub.")
        except DatahiveError as e:
            typer.echo(f"Warning: could not verify token ({e}). Saving anyway.", err=True)

    try:
        path = save_config(cfg)
    except DatahiveError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    typer.echo(f"Wrote config to {path} (0600).")
    maybe_write_gitignore(Path.cwd())


@app.command("new-profile")
def new_profile(samples: Optional[str] = SAMPLES_OPTION, force: bool = typer.Option(False, "--force")):
    """Creates samples/robot_profile.yaml, filled in ONCE for this rig."""
    root = _samples_opt(samples)
    try:
        path = write_profile_skeleton(root, force=force)
    except FileExistsError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)
    typer.echo(f"Wrote profile skeleton to {path}. Fill it in before recording episodes.")


@app.command()
def validate(episode_id: str, samples: Optional[str] = SAMPLES_OPTION, json: bool = typer.Option(False, "--json")):
    """Local schema + profile checks for one episode."""
    from datahive.validate import validate_episode

    root = _samples_opt(samples)
    try:
        warnings = validate_episode(root, episode_id)
    except DatahiveError as e:
        if json:
            typer.echo(jsonlib.dumps({"ok": False, "error": str(e)}))
        else:
            typer.echo(str(e), err=True)
        raise typer.Exit(1)

    if json:
        typer.echo(jsonlib.dumps({"ok": True, "warnings": warnings}))
    else:
        typer.echo(f"Episode '{episode_id}' is valid.")
        for w in warnings:
            typer.echo(f"  warning: {w}")


@app.command()
def annotate(
    episode_id: str,
    samples: Optional[str] = SAMPLES_OPTION,
    outcome: Optional[str] = typer.Option(None),
    failure_cause: Optional[str] = typer.Option(None),
    completion_time_s: Optional[float] = typer.Option(None),
    n_attempts: Optional[int] = typer.Option(None),
    n_regrasps: Optional[int] = typer.Option(None),
    stage_reached: Optional[int] = typer.Option(None),
    strategy: Optional[str] = typer.Option(None),
    attachment_id: Optional[str] = typer.Option(None),
    notes: str = typer.Option(""),
    non_interactive: bool = typer.Option(False, "--non-interactive"),
):
    """Fills outcome/failure_cause/etc. for an episode's trial, via terminal
    prompts (unless --non-interactive and all fields are given as flags)."""
    from datahive.annotate import annotate_episode

    root = _samples_opt(samples)

    def prompt_if_needed(value, label, **kwargs):
        if value is not None or non_interactive:
            return value
        return typer.prompt(label, **kwargs)

    outcome = prompt_if_needed(outcome, f"outcome ({'/'.join(o.value for o in Outcome)})")
    attachment_id = prompt_if_needed(attachment_id, "attachment_id")
    strategy = prompt_if_needed(strategy, f"strategy ({'/'.join(s.value for s in Strategy)})")
    if outcome != Outcome.success.value:
        failure_cause = prompt_if_needed(
            failure_cause, f"failure_cause ({'/'.join(c.value for c in FailureCause)})"
        )
    else:
        completion_time_s = prompt_if_needed(completion_time_s, "completion_time_s", type=float)
    if not non_interactive and n_attempts is None:
        n_attempts = typer.prompt("n_attempts", type=int, default=1)
    if not non_interactive and n_regrasps is None:
        n_regrasps = typer.prompt("n_regrasps", type=int, default=0)
    if not non_interactive and stage_reached is None:
        raw = typer.prompt("stage_reached (blank if not composed-assembly)", default="")
        stage_reached = int(raw) if raw else None

    fields = {
        "attachment_id": attachment_id,
        "outcome": outcome,
        "failure_cause": failure_cause,
        "completion_time_s": completion_time_s,
        "n_attempts": n_attempts,
        "n_regrasps": n_regrasps,
        "stage_reached": stage_reached,
        "strategy": strategy,
        "notes": notes,
    }
    try:
        annotate_episode(root, episode_id, fields)
    except DatahiveError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    typer.echo(f"Annotation saved for episode '{episode_id}'.")


@app.command()
def upload(episode_id: str, samples: Optional[str] = SAMPLES_OPTION, force: bool = typer.Option(False, "--force")):
    """Upload one episode to the Hub."""
    root = _samples_opt(samples)
    try:
        result = ops.upload_episode(root, episode_id, force=force)
    except DatahiveError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    if result.uploaded:
        typer.echo(f"Uploaded episode '{episode_id}' ({len(result.remote_paths)} files).")
    elif result.error:
        typer.echo(f"Upload failed for '{episode_id}': {result.error}", err=True)
        raise typer.Exit(1)
    else:
        typer.echo(f"Skipped '{episode_id}': {result.skipped_reason}")


@app.command()
def sync(samples: Optional[str] = SAMPLES_OPTION, dry_run: bool = typer.Option(False, "--dry-run"), json: bool = typer.Option(False, "--json")):
    """Reconcile all of samples/ with the Hub."""
    root = _samples_opt(samples)
    try:
        report = ops.sync(root, dry_run=dry_run)
    except DatahiveError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    if json:
        typer.echo(jsonlib.dumps(report.__dict__))
        return

    if report.newly_recorded:
        typer.echo(f"Newly recorded: {', '.join(report.newly_recorded)}")
    if report.uploaded:
        typer.echo(f"Uploaded: {', '.join(report.uploaded)}")
    if report.upload_failed:
        typer.echo(f"Upload failed: {', '.join(report.upload_failed)}")
    if report.remote_only:
        typer.echo(f"Remote only (not downloaded, review manually): {', '.join(report.remote_only)}")
    if not any([report.newly_recorded, report.uploaded, report.upload_failed, report.remote_only]):
        typer.echo("Nothing to do; everything is in sync.")


@app.command("list")
def list_cmd(
    samples: Optional[str] = SAMPLES_OPTION,
    status: Optional[str] = typer.Option(None),
    remote: bool = typer.Option(False, "--remote/--no-remote"),
    json: bool = typer.Option(False, "--json"),
):
    """Local + remote status per episode."""
    root = _samples_opt(samples)
    try:
        episodes = ops.list_episodes(root, include_remote=remote, status=status)
    except DatahiveError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    if json:
        typer.echo(jsonlib.dumps([e.__dict__ for e in episodes]))
        return

    if not episodes:
        typer.echo("No episodes found.")
        return
    for e in episodes:
        remote_tag = "" if e.remote is None else (" [remote]" if e.remote else " [local-only]")
        typer.echo(f"{e.episode_id}\t{e.session_id}\t{e.status}{remote_tag}")


@app.command()
def delete(
    episode_id: str,
    samples: Optional[str] = SAMPLES_OPTION,
    yes: bool = typer.Option(False, "--yes"),
    keep_remote: bool = typer.Option(False, "--keep-remote"),
):
    """Delete an episode locally and (if uploaded) from the Hub."""
    root = _samples_opt(samples)
    if not yes:
        confirmed = typer.confirm(f"Delete episode '{episode_id}' locally and from the Hub?")
        if not confirmed:
            raise typer.Abort()
    try:
        result = ops.delete_episode(root, episode_id, delete_remote=not keep_remote)
    except DatahiveError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    typer.echo(
        f"Deleted '{episode_id}' (local={result.deleted_local}, remote={result.deleted_remote})."
    )


@app.command()
def serve(port: int = typer.Option(8000, "--port"), samples: Optional[str] = SAMPLES_OPTION):
    """Launch the local web GUI (binds to localhost only)."""
    import uvicorn

    from datahive.interface.app import create_app

    root = _samples_opt(samples)
    application = create_app(root)
    uvicorn.run(application, host="127.0.0.1", port=port)


def main() -> None:
    try:
        app()
    except DatahiveError as e:
        typer.echo(str(e), err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
