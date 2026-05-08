# Dataset Management

## Pulling datasets

```bash
dvc pull
```

Requires `GDRIVE_SERVICE_ACCOUNT_JSON` to point to a valid service account JSON file:

```bash
export GDRIVE_SERVICE_ACCOUNT_JSON=/path/to/sa.json
dvc pull
```

## Adding new images

```bash
dvc add datasets/fire_smoke/images/new_batch/
git add datasets/fire_smoke/images/new_batch.dvc datasets/.gitignore
git commit -m "data: add fire_smoke batch vN"
dvc push
```

## Delta versioning convention

New feedback batches go into versioned subdirectories:

```
datasets/fire_smoke/feedback_v1/
datasets/fire_smoke/feedback_v2/
...
```

Each version is tracked as a separate `.dvc` file and committed to git.

## Person re-ID

`datasets/person_reid/` is **local only** and is excluded from all DVC pushes
via `datasets/person_reid/.dvcignore`. Never commit raw person re-ID images to git
or push to the shared remote.

## CI / headless pull

GitHub Actions uses a service account instead of OAuth. Add the JSON key as the
`GDRIVE_SERVICE_ACCOUNT_JSON` GitHub secret. The workflow sets:

```yaml
env:
  GDRIVE_SERVICE_ACCOUNT_JSON: ${{ secrets.GDRIVE_SERVICE_ACCOUNT_JSON }}
```

Then runs `dvc pull` with no interactive prompts.
