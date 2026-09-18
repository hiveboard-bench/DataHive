# DataHive

`datahive-tools`: the client-side package a lab installs to collect,
validate, annotate, and upload HiveBoard manipulation episodes to its
private Hugging Face dataset repo.

## Install

```
pip install -e .
```

## Quickstart

```
datahive init                 # one-time: writes ~/.datahive/config.yaml (0600)
datahive new-profile          # writes samples/robot_profile.yaml -- fill in ONCE
# ... record episodes into samples/{session}/episodes/{episode}.h5 + .mp4 ...
datahive annotate <episode_id>
datahive validate <episode_id>
datahive upload <episode_id>
datahive sync                 # reconcile all of samples/ with the Hub
datahive list
datahive delete <episode_id>
datahive serve --port 8000    # local web GUI, localhost only
```

See `docs/MANUAL_SMOKE_TEST.md` for the manual (non-automated) end-to-end
check against a real, disposable Hugging Face repo.

## Tests

```
pip install -e ".[dev]"
pytest
```

All tests run with no real network access and no real Hugging Face
credentials.
