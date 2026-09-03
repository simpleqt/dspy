# Versioned documentation

DSPy's versioned documentation is one static site managed by the Zensical
team's [Mike fork](https://github.com/squidfunk/mike):

- `/` redirects to `/current/`.
- `/current/` is the mutable documentation built from `main`.
- `/X.Y.Z/` is a release snapshot built from tag `X.Y.Z` while importing the
  exact released DSPy wheel.
- `/X.Y/` redirects to the newest imported patch in that minor line.

The picker lists Current and every patch release. Minor aliases are navigation
conveniences and are hidden from the picker. Mike owns `versions.json`, the
default redirect, aliases, and version directories on the deployed `master`
branch in `krypticmouse/dspy-docs`.

Historical snapshots use Material for MkDocs, while Current and new release
snapshots use Zensical. Stored static versions do not need to share a renderer.

## Deployment

Mike commits generated output to `master` in `krypticmouse/dspy-docs`, which is
deployed as the static site. The publisher requires the Current entry in
`versions.json` to identify Zensical before it can update Current or add a
release. This prevents a workflow from writing to an uninitialized or
incompatible deployment.

Existing unversioned page URLs remain valid. Publishing Current generates root
redirect pages such as `/api/` → `/current/api/`, and each build scopes
hand-authored root-relative links to its own version so an old page cannot
silently jump into Current. Query strings and fragments survive redirects.

## Production publication

Current publication and release publication fail closed unless Mike's metadata
identifies Zensical as the Current renderer.

After a stable `dspy` wheel reaches PyPI, the release workflow preserves that
exact wheel, builds `/X.Y.Z/` from the tag, and publishes it through Mike. The
`dspy-ai` compatibility package publishes in a separate downstream job, so its
failure cannot suppress documentation for an already-published `dspy` wheel.
Release-tag jobs never use GitHub's lossy pending-concurrency slot. Mutable
Current keeps latest-wins serialization because a newer `main` build includes
the superseded commit. Deployment writes retry optimistic Git pushes; every
release rechecks the Zensical promotion marker after refetching,
and a delayed older patch cannot move an `/X.Y/` alias backward.

Corrections and rollbacks use reviewed pull requests in the deployment
repository. Restore a known-good tree with a new commit rather than rewriting
production history.

## Historical fidelity

Versions 3.0 through 3.3 use the documentation source and Material
configuration from their release tags. Their requirements were not fully
pinned and referred to DSPy on the moving `main` branch. Bootstrap replaces
that dependency with the release wheel and resolves the remaining requirements
no later than the tag's commit time.

The tags' `uv.lock` files describe DSPy's project and development dependencies,
not the separate toolchain in `docs/requirements.txt`. They do not lock MkDocs,
Material, Jupyter, redirects, mkdocstrings, or llmstxt.

Each snapshot contains `_meta/build.json` with its source tag and commit,
renderer version, DSPy artifact source and SHA-256, complete resolved Python
package set, and known reconstruction differences.

PyPI never published `dspy==3.1.1`. That snapshot is the sole exception: its
wheel is built from tag `3.1.1` using the release workflow's metadata
substitutions and is marked `tag-built-wheel`.

The original generated deployments were not archived, so reconstructed HTML
is not expected to be byte-identical. The compatibility contract is routes,
redirects, anchors, content, notebooks, matching API symbols, search behavior,
metadata, navigation, and user-facing interactions. The version selector,
build metadata, conservative HTML minification, and omitted source maps are
intentional additions.

## Historical corrections

Release snapshots are immutable to automation, not permanent write-once
storage. An identical retry is a no-op; a retry with different output fails
before Mike can replace `/X.Y.Z/`.

An intentional correction is a reviewed pull request directly against
`krypticmouse/dspy-docs`, normally limited to the affected version directory.
That repository's pull request and Git history provide audit and rollback.

## Output size

Minor aliases contain redirects rather than duplicate assets. Production
builds remove source maps and conservatively minify HTML while preserving
whitespace-sensitive elements. Git deduplicates byte-identical objects in the
deployment repository. Browsers request only the selected page and its assets;
they do not download the aggregate repository.

## Preserved site features

Zensical does not directly implement every output from the former Material
pipeline. The production builder preserves those features explicitly:

| Existing feature | Zensical path |
| --- | --- |
| API reference | `mkdocstrings` using the installed DSPy package |
| Notebooks | pre-render with `nbconvert` in a disposable source tree |
| Redirects | emit equivalent static redirects after rendering |
| Social cards | generate per-page cards and inject matching metadata |
| `llms.txt` | generate from the same configured source inventory |
| Build-time statistics | run the existing fetcher before rendering |
| Search | Zensical's built-in search index |
| Custom tabs override | use Zensical's built-in tabs implementation |

Focused tests cover these compatibility boundaries, and every documentation
pull request performs a full Zensical build. Renderer-specific typography,
spacing, wrapping, code rendering, search ranking, and social-card appearance
may differ without dropping a feature.
