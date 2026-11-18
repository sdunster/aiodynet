# Releasing aiodynet

Releases go to [PyPI](https://pypi.org/project/aiodynet/) from GitHub
Actions, using PyPI trusted publishing: no API token is stored anywhere.
Publishing a GitHub release whose tag is `vX.Y.Z` does everything else.

## Choosing the version

aiodynet follows [semantic versioning](https://semver.org/). Before 1.0:

- **Patch** (`0.1.0` → `0.1.1`): bug fixes, and new message types that only
  turn packets which used to decode as `UnknownMessage` into typed messages.
- **Minor** (`0.1.0` → `0.2.0`): anything that changes existing public API or
  behaviour, such as renaming or removing a class or field, changing what a
  packet decodes to, or changing the bytes a message encodes to.

Home Assistant pins an exact version, so it only picks up a release when
someone bumps that pin. Avoid any change to the bytes an existing message
sends, because installs in the field depend on them. If one is unavoidable,
call it out in the changelog.

## Steps

1. **Start from a green `main`.** Check that the latest CI run on `main`
   passed:

   ```sh
   git switch main && git pull
   gh run list --branch main --limit 1
   ```

2. **Bump the version.** This updates both `pyproject.toml` and `uv.lock`:

   ```sh
   uv version --bump patch      # or: minor, major, or an exact version
   ```

3. **Update `CHANGELOG.md`.** Add a section at the top in this form:

   ```markdown
   ## 0.1.1 (2026-12-01)

   - What changed, written for people using the library.
   ```

4. **Commit and push**, then wait for CI to pass on that commit:

   ```sh
   git commit -am "Release 0.1.1"
   git push
   gh run watch "$(gh run list --branch main --limit 1 --json databaseId -q '.[0].databaseId')"
   ```

5. **Publish the GitHub release.** The tag must be `v` plus the exact
   version in `pyproject.toml`:

   ```sh
   gh release create v0.1.1 --target main --title "aiodynet 0.1.1" \
     --notes "Short summary; see CHANGELOG.md."
   ```

   Or use *Releases → Draft a new release* on GitHub, create the tag
   `v0.1.1` on `main`, and click *Publish release*. Saving a draft doesn't
   start anything; only publishing does.

6. **Watch the release workflow** until it succeeds:

   ```sh
   gh run watch "$(gh run list --workflow release.yml --limit 1 --json databaseId -q '.[0].databaseId')"
   ```

7. **Check the result.** The new version should be on PyPI, with a wheel
   and an sdist, and a clean install should work:

   ```sh
   uv run --isolated --no-cache --no-project --with aiodynet==0.1.1 \
     python -c "import aiodynet; print('ok')"
   ```

   The GitHub release should also have the wheel and sdist attached.

## What the release workflow does

[`.github/workflows/release.yml`](.github/workflows/release.yml) runs when a
release is *published*. In order, it:

1. fails unless the tag is exactly `v` plus the version in `pyproject.toml`;
2. installs from the lockfile (`uv sync --locked`) and runs the tests;
3. builds the sdist and wheel, and checks them with `twine check --strict`;
4. publishes to PyPI with trusted publishing (`uv publish --trusted-publishing always`);
5. attaches the sdist and wheel to the GitHub release.

The job runs in the `pypi` GitHub environment, which only accepts tags
matching `v*`. A workflow run from a branch can't publish.

## When something goes wrong

- **The workflow fails before "publish"** (wrong tag, a failing test, a bad
  build). Nothing reached PyPI. Fix the problem on `main`, then delete the
  release and its tag and create them again:

  ```sh
  gh release delete v0.1.1 --cleanup-tag --yes
  ```

- **Publishing fails with a trusted-publishing error.** The PyPI side of the
  setup is missing or wrong; see [Trusted publisher](#trusted-publisher)
  below. Fix it on PyPI, then re-run the failed job
  (`gh run rerun <run-id> --failed`).

- **PyPI has the release but attaching the files to GitHub failed.** Don't
  re-run the job, because PyPI rejects a second upload of the same version.
  Build locally from the tag and attach the files by hand:

  ```sh
  git switch --detach v0.1.1 && rm -rf dist && uv build
  gh release upload v0.1.1 dist/*
  git switch main
  ```

- **A broken version reached PyPI.** A version number can never be uploaded
  twice, even after deleting it. Release a fixed patch version. If the broken
  one is harmful, also *yank* it on PyPI (project → *Manage* → *Releases* →
  *Options* → *Yank*), which hides it from new installs without breaking
  existing pins.

## Trusted publisher

PyPI accepts uploads from this repository through a trusted publisher. If it
ever needs recreating, it's under *aiodynet → Manage → Publishing* on PyPI,
with these settings:

| Field | Value |
|---|---|
| Owner | `sdunster` |
| Repository name | `aiodynet` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

The GitHub side is the `pypi` environment in *Settings → Environments*. Its
deployment rule allows only tags matching `v*`.

## After a release

- **dynet-cli:** if it needs the new version, raise its `aiodynet>=`
  requirement and run `uv lock --upgrade-package aiodynet` there. See its
  `RELEASING.md`.
- **Home Assistant core:** bump the pin in
  `homeassistant/components/dynalite/manifest.json`, then run
  `python -m script.gen_requirements_all`, and open a core PR with the
  changelog.
