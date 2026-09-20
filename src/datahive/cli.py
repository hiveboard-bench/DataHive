"""`datahive` CLI. Thin: parses arguments, calls the shared library/ops
functions, formats output. No business logic lives here."""

from __future__ import annotations

import json as jsonlib
import sys
from pathlib import Path
from typing import Optional

import typer

from datahive import __version__, ops
from datahive.config import (
    Config,
    check_permissions,
    config_path,
    default_repo_id,
    load_config,
    masked_dict,
    maybe_write_gitignore,
    now_iso,
    save_config,
)
from datahive.consistency import is_placeholder_operator
from datahive.errors import DatahiveError
from datahive.hub import Hub
from datahive.index import Index
from datahive.paths import default_samples_root
from datahive.profile import write_profile_skeleton
from datahive.schema import FailureCause, Outcome, Strategy

import typer.rich_utils
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

_TYPICAL_SESSION_EPILOG = """\
  [bold]A typical session, in order:[/bold]

    [cyan]datahive init[/cyan]                  [dim]# once: lab id + Hugging Face token[/dim]
    [cyan]datahive new-profile[/cyan]           [dim]# once per robot, then fill the file in[/dim]
    [cyan]datahive check[/cyan]    <episode_id> [dim]# pre-annotation check (frames, Hz, MP4s)[/dim]
    [cyan]datahive annotate[/cyan] <episode_id> [dim]# fill outcome/failure cause/strategy[/dim]
    [cyan]datahive validate[/cyan] <episode_id> [dim]# local schema + profile checks[/dim]
    [cyan]datahive upload[/cyan]   <episode_id> [dim]# upload one episode to the Hub[/dim]

  [dim]Run 'datahive <command> --help' for one command's options.[/dim]"""

_orig_rich_format_help = typer.rich_utils.rich_format_help


def _custom_rich_format_help(*, obj, ctx, markup_mode):
    epilog = obj.epilog
    obj.epilog = None
    try:
        _orig_rich_format_help(obj=obj, ctx=ctx, markup_mode=markup_mode)
    finally:
        obj.epilog = epilog
    if epilog:
        console = typer.rich_utils._get_rich_console()
        console.print(Text.from_markup(epilog.strip("\n")))


typer.rich_utils.rich_format_help = _custom_rich_format_help

_APP_HELP = """\
Record, annotate, validate and publish robotic manipulation rollouts.

Start with 'datahive init'."""

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=_APP_HELP,
    epilog=_TYPICAL_SESSION_EPILOG,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"datahive, version {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
    config_dir: Optional[str] = typer.Option(
        None,
        "--config-dir",
        metavar="DIR",
        help=(
            "Directory holding contributor_config.yaml "
            "(overrides $OOPSIE_CONFIG_DIR for this invocation; robot profiles are unaffected)"
        ),
    ),
) -> None:
    if config_dir:
        import os

        os.environ["DATAHIVE_CONFIG_HOME"] = config_dir
        os.environ["OOPSIE_CONFIG_DIR"] = config_dir

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(2)


def _samples_opt(samples: Optional[str]) -> Path:
    return Path(samples).resolve() if samples else default_samples_root()


SAMPLES_OPTION = typer.Option(None, "--samples", help="Path to the samples/ directory (default: ./samples)")


@app.command()
def init(
    lab_id: str = typer.Option(..., prompt=True),
    repo_id: Optional[str] = typer.Option(None, help="Advanced. Defaults to HiveBoard/{lab_id}"),
    token: str = typer.Option(..., prompt=True, hide_input=True, help="Hugging Face token"),
    endpoint: Optional[str] = typer.Option(None, help="Custom HF endpoint (advanced)"),
    config_dir: Optional[str] = typer.Option(
        None,
        "--config-dir",
        metavar="DIR",
        help=(
            "Directory holding contributor_config.yaml (overrides $OOPSIE_CONFIG_DIR for this "
            "invocation; robot profiles are unaffected)"
        ),
    ),
    no_verify: bool = typer.Option(False, "--no-verify", help="Skip the whoami() check"),
    force: bool = typer.Option(False, "--force"),
):
    """Set up the lab config (lab id + Hugging Face token)."""
    if config_dir:
        import os

        os.environ["DATAHIVE_CONFIG_HOME"] = config_dir
        os.environ["OOPSIE_CONFIG_DIR"] = config_dir

    target = config_path()
    if target.exists() and not force:
        typer.echo(f"Config already exists at {target}. Pass --force to overwrite.", err=True)
        raise typer.Exit(2)

    repo_id = repo_id or default_repo_id(lab_id)
    cfg = Config(
        lab_id=lab_id, repo_id=repo_id, hf_token=token,
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
def check(
    episode_id: Optional[str] = typer.Argument(None, help="Specific episode_id to check (default: check all recorded)"),
    samples: Optional[str] = SAMPLES_OPTION,
    json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Pre-annotation health check for recorded episodes (HDF5, Hz, MP4s)."""
    from datahive.check import check_all_episodes, check_episode

    root = _samples_opt(samples)
    if episode_id:
        results = [check_episode(root, episode_id)]
    else:
        results = check_all_episodes(root)

    if not results:
        if json:
            typer.echo(jsonlib.dumps([]))
        else:
            typer.echo("No episodes found to check.")
        return

    if json:
        typer.echo(jsonlib.dumps(results if not episode_id else results[0]))
        if any(not r["ok"] for r in results):
            raise typer.Exit(1)
        return

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan")
    table.add_column("Episode", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Steps", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Rate (Hz)", justify="right")
    table.add_column("Cameras", justify="center")
    table.add_column("Issues")

    has_failures = False
    for r in results:
        ok = r["ok"]
        if not ok:
            has_failures = True
            status_text = "[bold red]FAIL[/bold red]"
        elif r["warnings"]:
            status_text = "[bold yellow]WARN[/bold yellow]"
        else:
            status_text = "[bold green]PASS[/bold green]"

        stats = r["stats"]
        steps = str(stats["n_steps"]) if stats["n_steps"] else "–"
        dur = f"{stats['duration_s']:.1f}s" if stats["duration_s"] is not None else "–"
        hz = f"{stats['sample_rate_hz']:.0f} Hz" if stats["sample_rate_hz"] is not None else "–"
        cams = str(len(stats["cameras"]))

        issues = []
        for p in r["problems"]:
            issues.append(f"[red]• {p}[/red]")
        for w in r["warnings"]:
            issues.append(f"[yellow]• {w}[/yellow]")
        issue_str = "\n".join(issues) if issues else "[dim]None[/dim]"

        table.add_row(r["episode_id"], status_text, steps, dur, hz, cams, issue_str)

    console = Console()
    console.print(table)

    if has_failures:
        raise typer.Exit(1)


@app.command()
def annotate(
    episode_id: str,
    samples: Optional[str] = SAMPLES_OPTION,
    outcome: Optional[str] = typer.Option(None),
    failure_cause: Optional[str] = typer.Option(None),
    failure_cause_detail: Optional[str] = typer.Option(None, help="Free-text reason, required when failure_cause=other"),
    severity: Optional[str] = typer.Option(None, help="minor | moderate | critical (only for non-success outcomes)"),
    completion_time_s: Optional[float] = typer.Option(None),
    n_attempts: Optional[int] = typer.Option(None),
    n_regrasps: Optional[int] = typer.Option(None),
    stage_reached: Optional[int] = typer.Option(None),
    strategy: Optional[str] = typer.Option(None),
    attachment_id: Optional[str] = typer.Option(None),
    operator_name: Optional[str] = typer.Option(None, help="Who ran this trial"),
    annotator_name: Optional[str] = typer.Option(None, help="Who is filling in this annotation"),
    notes: str = typer.Option(""),
    non_interactive: bool = typer.Option(False, "--non-interactive"),
):
    """Fills outcome/failure_cause/etc. for an episode's trial, via terminal
    prompts (unless --non-interactive and all fields are given as flags)."""
    from datahive.annotate import annotate_episode
    from datahive.schema import FailureSeverity

    root = _samples_opt(samples)

    def prompt_if_needed(value, label, **kwargs):
        if value is not None or non_interactive:
            return value
        return typer.prompt(label, **kwargs)

    outcome = prompt_if_needed(outcome, f"outcome ({'/'.join(o.value for o in Outcome)})")
    attachment_id = prompt_if_needed(attachment_id, "attachment_id")
    strategy = prompt_if_needed(strategy, f"strategy ({'/'.join(s.value for s in Strategy)})")
    operator_name = prompt_if_needed(operator_name, "operator_name (who ran this trial)")
    annotator_name = prompt_if_needed(annotator_name, "annotator_name (who is filling this in)")
    if not (operator_name or "").strip() or not (annotator_name or "").strip():
        typer.echo("operator_name and annotator_name are both required (pass --operator-name and --annotator-name).", err=True)
        raise typer.Exit(1)
    if is_placeholder_operator(operator_name):
        typer.echo("operator_name cannot be the placeholder 'Unassigned': enter who actually ran the trial.", err=True)
        raise typer.Exit(1)
    if outcome != Outcome.success.value:
        failure_cause = prompt_if_needed(
            failure_cause, f"failure_cause ({'/'.join(c.value for c in FailureCause)})"
        )
        if failure_cause == FailureCause.other.value:
            failure_cause_detail = prompt_if_needed(failure_cause_detail, "failure_cause_detail (what happened)")
        if not non_interactive and severity is None:
            raw = typer.prompt(f"severity ({'/'.join(s.value for s in FailureSeverity)}, blank to skip)", default="")
            severity = raw or None

    if not non_interactive and n_attempts is None:
        n_attempts = typer.prompt("n_attempts", type=int, default=1)
    if not non_interactive and n_regrasps is None:
        n_regrasps = typer.prompt("n_regrasps", type=int, default=0)
    if not non_interactive and stage_reached is None:
        raw = typer.prompt("stage_reached (blank if not composed-assembly)", default="")
        stage_reached = int(raw) if raw else None

    fields = {
        "attachment_id": attachment_id,
        "operator_name": operator_name or "",
        "outcome": outcome,
        "failure_cause": failure_cause,
        "failure_cause_detail": failure_cause_detail or "",
        "severity": severity,
        "completion_time_s": completion_time_s,
        "n_attempts": n_attempts,
        "n_regrasps": n_regrasps,
        "stage_reached": stage_reached,
        "strategy": strategy,
        "annotator_name": annotator_name or "",
        "notes": notes,
    }
    try:
        annotate_episode(root, episode_id, fields)
    except DatahiveError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    typer.echo(f"Annotation saved for episode '{episode_id}'.")


def format_preflight(summary: dict) -> str:
    size_mb = summary["total_bytes"] / (1024 * 1024)
    lines = [
        f"{summary['n_episodes']} episode(s), {size_mb:.1f} MB | "
        f"{summary['n_unannotated']} unannotated | {summary['n_invalid']} failing validation | "
        f"{summary['n_warnings']} warning(s)"
    ]
    for ep in summary["episodes"]:
        for p in ep["problems"]:
            lines.append(f"  ! {ep['episode_id']}: {p}")
        for w in ep["warnings"]:
            lines.append(f"  ~ {ep['episode_id']}: {w}")
    lines += [f"  ! {p}" for p in summary["directory_problems"]]
    lines += [f"  ~ {w}" for w in summary["directory_warnings"]]
    lines += [f"  ~ diversity: {d}" for d in summary["diversity"]]
    return "\n".join(lines)


@app.command()
def upload(
    episode_id: str,
    samples: Optional[str] = SAMPLES_OPTION,
    force: bool = typer.Option(False, "--force"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
    strict_diversity: bool = typer.Option(False, "--strict-diversity", help="Treat diversity warnings as an error"),
):
    """Upload one episode to the Hub (shows a summary first)."""
    from datahive.preflight import upload_preflight

    root = _samples_opt(samples)
    try:
        summary = upload_preflight(root, [episode_id], hub=None)
    except DatahiveError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    typer.echo(format_preflight(summary))
    if summary["directory_problems"] or (strict_diversity and summary["diversity"]):
        typer.echo("Aborting: fix the problems above first.", err=True)
        raise typer.Exit(1)
    if not yes and not typer.confirm("Upload?", default=True):
        raise typer.Exit(1)
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
    if dry_run and report.skipped:
        typer.echo(f"Would upload: {', '.join(report.skipped)}")
    if report.uploaded:
        typer.echo(f"Uploaded: {', '.join(report.uploaded)}")
    if report.upload_failed:
        typer.echo(f"Upload failed: {', '.join(report.upload_failed)}")
    if report.remote_only:
        typer.echo(f"Remote only (not downloaded, review manually): {', '.join(report.remote_only)}")
    if not any([report.newly_recorded, report.uploaded, report.upload_failed, report.remote_only, dry_run and report.skipped]):
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

    status_colors = {
        "recorded": "yellow",
        "validated": "cyan",
        "uploaded": "green",
        "upload_failed": "red",
    }

    table = Table(
        title="DataHive Episodes",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="dim",
        title_style="bold",
    )
    table.add_column("Episode ID", style="bold white")
    table.add_column("Session", style="dim")
    table.add_column("Status")
    if remote:
        table.add_column("Location")

    for e in episodes:
        color = status_colors.get(e.status, "white")
        status_styled = f"[{color}]{e.status}[/{color}]"
        if remote:
            loc = "[green]remote[/green]" if e.remote else "[yellow]local-only[/yellow]"
            table.add_row(e.episode_id, e.session_id, status_styled, loc)
        else:
            table.add_row(e.episode_id, e.session_id, status_styled)

    Console().print(table)


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


@app.command("serve", hidden=True)
@app.command("server", hidden=True)
@app.command("interface")
def serve(port: int = typer.Option(8000, "--port"), samples: Optional[str] = SAMPLES_OPTION):
    """Launch the local web GUI (binds to localhost only)."""
    import uvicorn

    from datahive.interface.app import create_app

    root = _samples_opt(samples)
    application = create_app(root)
    uvicorn.run(application, host="127.0.0.1", port=port)


interface = serve


def main() -> None:
    try:
        app()
    except DatahiveError as e:
        typer.echo(str(e), err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
