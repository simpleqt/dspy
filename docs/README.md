# DSPy documentation

The published documentation is at [dspy.ai](https://dspy.ai). This directory
contains its source and build tooling.

## Build locally

From the repository root:

```bash
pip install -r docs/requirements.txt -e .
python docs/scripts/generate_api_docs.py
python docs/scripts/generate_api_summary.py
cd docs
python scripts/build_docs.py current
```

The static site is written to `docs/site`. Serve it with any static HTTP
server, for example:

```bash
python -m http.server --directory site 8000
```

The build uses [Zensical](https://zensical.org/). `mkdocs.yml` is the native
site configuration. `build.yml` contains settings for outputs that the
builder preserves outside Zensical: redirects and `llms.txt`. Notebooks are
converted with `nbconvert` in a disposable copy before Zensical runs; source
notebooks are never modified.

Pull requests that change documentation run the same production build. Fix a
failed documentation build before merging.

## Publication

Merges to `main` rebuild mutable Current documentation. Stable release tags
build an immutable patch snapshot from the tag while importing the exact wheel
published by that release workflow. See [versioning/README.md](versioning/README.md)
for the URL, storage, release, and correction contracts.
