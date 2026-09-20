# Contributing to DataHive

Thank you for helping improve DataHive. This guide walks you through everything, from reporting a
problem to getting a pull request merged. You do not need to be an expert: small fixes, typo
corrections, better error messages and extra tests are all welcome.

DataHive (`datahive-tools`) is the client-side toolkit a lab uses to collect, validate, annotate
and upload HiveBoard manipulation episodes. Its pipeline and design are adapted from
[Oopsie Data](https://github.com/oopsie-data); please keep that credit intact.

## Contents

1. [Ways to contribute](#ways-to-contribute)
2. [Open an issue](#open-an-issue)
3. [Set up your machine](#set-up-your-machine)
4. [Make a change](#make-a-change)
5. [Open a pull request](#open-a-pull-request)
6. [Project map](#project-map)
7. [Coding guidelines](#coding-guidelines)
8. [Working on the interface](#working-on-the-interface)
9. [Working on the AI assistant skills](#working-on-the-ai-assistant-skills)
10. [Security](#security)

## Ways to contribute

- **Report a bug** or a confusing message.
- **Suggest a feature** or an improvement to the workflow.
- **Improve the docs**: the README, this guide, or the two skills under `src/datahive/skills/`.
- **Fix a bug or add a feature** with code and tests.
- **Try it on your own robot** and tell us what was hard. Feedback from real labs is the most useful
  thing you can give.

## Open an issue

Search the [existing issues](https://github.com/hiveboard-bench/DataHive/issues) first: your problem
may already be there, and you can add details or a reaction instead of opening a duplicate.

To open a new one, go to **Issues → New issue** and pick a template.

**A good bug report has:**

- what you did, step by step, and what you expected to happen;
- what happened instead, with the exact error text;
- your setup: `datahive --version`, Python version, operating system, and browser if it is about the
  interface;
- for data problems, the output of `datahive check <episode_id> --json` or
  `datahive validate <episode_id> --json`.

**Never paste secrets.** Remove your Hugging Face token and anything from `~/.datahive/config.yaml`.
Do not attach private recordings; describe them instead (duration, rate, number of cameras).

**Feature requests** work best when you describe the problem you are trying to solve before the
solution you have in mind.

For anything about vulnerabilities, see [Security](#security) instead of opening a public issue.

## Set up your machine

You need Python 3.10 or newer and [Git](https://git-scm.com/). A free GitHub account is required.

### 1. Fork the repository

On <https://github.com/hiveboard-bench/DataHive>, click **Fork** (top right) and create the fork under
your own account. Your fork is your personal copy: you can change it freely.

### 2. Clone your fork

```bash
git clone https://github.com/<your-username>/DataHive.git
cd DataHive
```

### 3. Add the original repository as `upstream`

This lets you keep your fork up to date with the project.

```bash
git remote add upstream https://github.com/hiveboard-bench/DataHive.git
git remote -v        # origin = your fork, upstream = hiveboard-bench/DataHive
```

### 4. Create a virtual environment and install

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"              # editable install + pytest, httpx and OpenCV
datahive --version
```

### 5. Check that everything works

```bash
pytest
```

All tests run without network access and without real Hugging Face credentials, so they are safe to
run anywhere. If they do not pass on a fresh clone, that is worth an issue.

## Make a change

### 1. Sync, then branch

Never work directly on `main`. Start from an up-to-date copy:

```bash
git checkout main
git pull upstream main
git checkout -b fix/short-description      # or feat/..., docs/..., test/...
```

Branch name prefixes: `fix/`, `feat/`, `docs/`, `test/`, `chore/`.

### 2. Change the code, add tests

- Add or update a test for every behavior change. Tests live in `tests/`; look at a neighbouring test
  for the fixtures you can reuse (`samples_root`, `filled_profile`, `fake_hub`, `make_episode`).
- Tests must never touch the network. Hub calls go through a fake, see `tests/conftest.py`.
- Run `pytest` (or a single file, `pytest tests/test_check.py`) until it is green.

### 3. Try it for real

```bash
datahive interface --port 8000       # the web interface, http://127.0.0.1:8000
datahive check <episode_id>          # the CLI
```

Use a scratch folder (`--samples /tmp/scratch`) so you do not touch real data.

### 4. Commit

Small, focused commits are easier to review. We write commit messages in the imperative, with a type
prefix:

```
fix: reject episodes with NaN values in check
feat: add --json output to datahive list
docs: explain the samples/ layout
test: cover sync --dry-run
chore: bump dependencies
```

Use `feat(ui):` for interface changes if it helps. Keep the first line under about 72 characters, and
explain the *why* in the body when it is not obvious.

```bash
git status
git add path/to/file            # or: git add -p   to pick individual changes
git commit -m "fix: short description"
```

Do not commit generated or private files: `dist/`, `samples/`, `.datahive/`, `uv.lock`, tokens.

## Open a pull request

### 1. Update your branch, then push it to your fork

```bash
git fetch upstream
git rebase upstream/main            # or: git merge upstream/main
pytest                              # once more, after the rebase
git push -u origin fix/short-description
```

### 2. Create the pull request

Open your fork on GitHub. A **Compare & pull request** banner appears for the branch you just pushed;
click it. Make sure that:

- the **base repository** is `hiveboard-bench/DataHive` and the base branch is `main`;
- the title follows the same style as a commit message;
- the description explains **what** changed and **why**, links the issue (`Closes #123`), and lists how
  you tested it. Screenshots help for anything visible.

Opening it as a **draft** is fine if you want early feedback.

### 3. Review

A maintainer will review your pull request. Expect questions and small requests; that is normal and
not personal. To address feedback, push more commits to the same branch and the pull request updates
by itself. When the review is done we will merge it (usually with a squash).

### After it is merged

```bash
git checkout main
git pull upstream main
git branch -d fix/short-description
git push origin --delete fix/short-description     # optional: tidy your fork
```

### Pull request checklist

- [ ] `pytest` passes.
- [ ] New behavior has a test.
- [ ] Docs or help text updated when behavior changed (README, `--help`, the skills).
- [ ] No secrets, private recordings, or generated files in the diff.
- [ ] For interface changes: checked on a phone-sized window too (see below).

## Project map

```
src/datahive/
  cli.py            the `datahive` command line (thin: parses arguments, calls the library)
  schema.py         episode header and trial annotation models and their rules
  profile.py        robot_profile.yaml: fields and completeness rules
  episode.py        writing and reading the .h5 episode files (EpisodeWriter)
  check.py          `datahive check`: recording health check, no annotation needed
  validate.py       `datahive validate`: full checks, incl. profile and annotation
  consistency.py    the data rules shared by check and validate (rate, video, NaN, ...)
  annotate.py       saving annotations (trials.csv and inside the .h5)
  runner.py         Runner sessions, trial plans and manual episode uploads
  collect.py        automatic collection: server state machine and CollectClient
  ops.py, hub.py    upload, sync and delete against the Hugging Face Hub
  attachments.yaml  the 13 HiveBoard tasks (ids, timeouts, stages)
  interface/        the local web app: api.py (FastAPI) and static/ (plain HTML, CSS, JS)
  skills/           the AI assistant skills that `datahive install-skill` installs
tests/              pytest suite (no network)
```

The CLI, the web interface and the library share the same code paths, so a fix in `check.py` or
`consistency.py` benefits all of them. Keep business logic out of `cli.py`.

## Coding guidelines

- **Match the surrounding code**: naming, structure and level of commenting. Keep comments short and
  only where the *why* is not obvious.
- Python 3.10 must keep working. Avoid 3.11-only features unless you add a fallback (see `StrEnum` in
  `schema.py`).
- **Never loosen a validation rule just to make data pass.** The rules exist to keep the benchmark
  comparable. If a rule looks wrong, open an issue to discuss it.
- Error messages are for lab members, not only developers: say what is wrong and how to fix it.
- Do not add a dependency without a good reason and a mention in the pull request.
- Never log or print the Hugging Face token. Config files are written with mode `0600`.

## Working on the interface

The interface is plain HTML, CSS and JavaScript in `src/datahive/interface/static/`. There is no build
step and no framework: edit a file, refresh the page.

```bash
datahive interface --port 8000 --samples /tmp/scratch
```

- After editing `style.css`, bump the `?v=` number on the stylesheet link in `index.html` so browsers
  reload it.
- **Check small screens.** The layout has to work on phones. Open the browser dev tools, toggle the
  device toolbar, and try widths of 360 and 768 px besides desktop. Look for horizontal scrolling,
  clipped buttons and text that overflows.
- Hover-only behavior (tooltips) must not be the only way to get an important piece of information,
  because phones have no hover.
- Test both light and dark themes.

## Working on the AI assistant skills

Two skills ship with the package, in `src/datahive/skills/`: `datahive-data-prep` and
`datahive-auto-collect`. They teach an AI assistant how to use DataHive.

- Keep them accurate: when you change a rule or a command, update the skill that mentions it.
- Facts must come from the code (thresholds, field names, messages), not from memory.
- Preserve the safety rules: assistants must not invent operator or annotator names, must not
  weaken checks, and must leave `upload`, `sync` and `delete` to the user.
- Run `datahive install-skill --dir /tmp/skills` to make sure they still install.

## Security

Please do not open a public issue for a security problem, such as a way to leak a token or to write
outside the `samples/` folder. Report it privately through GitHub's
[**Security → Report a vulnerability**](https://github.com/hiveboard-bench/DataHive/security/advisories/new)
form on the repository, or contact the maintainers directly. Include steps to reproduce and we will
respond as soon as we can.

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE)
that covers the project.

Thank you for contributing!
