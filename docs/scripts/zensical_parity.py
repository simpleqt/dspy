#!/usr/bin/env python3
"""Build Material and Zensical from identical inputs and compare their output."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from xml.etree import ElementTree

import yaml

if __package__:
    from .build_docs import install_shared_header_styles, optimize_site
    from .publish_versioned_docs import publish_site
    from .zensical_build import build_zensical_site
else:
    from build_docs import install_shared_header_styles, optimize_site
    from publish_versioned_docs import publish_site
    from zensical_build import build_zensical_site

REQUIRED_METADATA = (
    "canonical",
    "og:type",
    "og:title",
    "og:description",
    "og:url",
    "og:image",
    "og:image:type",
    "og:image:width",
    "og:image:height",
)


def remove_top_level_block(text: str, key: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    index = 0
    marker = f"{key}:"
    while index < len(lines):
        if lines[index].strip() == marker and not lines[index].startswith((" ", "\t")):
            index += 1
            while index < len(lines) and (not lines[index].strip() or lines[index].startswith((" ", "\t"))):
                index += 1
            continue
        output.append(lines[index])
        index += 1
    return "\n".join(output) + "\n"


def inject_stats(text: str, stats: dict[str, object]) -> str:
    marker = "extra:\n"
    if marker not in text:
        raise RuntimeError("mkdocs config has no top-level extra block")
    rendered = yaml.safe_dump({"stats": stats}, sort_keys=True, default_flow_style=False)
    indented = "".join(f"    {line}\n" for line in rendered.rstrip().splitlines())
    return text.replace(marker, marker + indented, 1)


def frozen_stats(project: Path) -> dict[str, object]:
    cache = project / ".cache" / "stats.json"
    if cache.exists():
        return {key: value for key, value in json.loads(cache.read_text()).items() if not key.startswith("_")}

    hook = project / "hooks" / "fetch_stats.py"
    spec = importlib.util.spec_from_file_location("dspy_docs_fetch_stats", hook)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {hook}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.fetch_stats())


def copy_project(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("site", ".cache", "__pycache__", ".zensical-*"),
    )


def freeze_material_config(config: Path, stats: dict[str, object], repository: Path) -> None:
    text = remove_top_level_block(config.read_text(), "hooks")
    text = inject_stats(text, stats)
    text = text.replace("extra:\n", "extra:\n    version:\n        provider: mike\n        alias: true\n", 1)
    text = text.replace('paths: [".."]', f'paths: ["{repository}"]')
    config.write_text(text)


def route_for_source(path: str) -> str:
    source = Path(path)
    if source.name == "index.md":
        route = source.parent.as_posix().strip("/")
    else:
        route = source.with_suffix("").as_posix().strip("/")
    if route == ".":
        route = ""
    return f"/{route}/" if route else "/"


def redirect_maps(config: Path) -> dict[str, str]:
    return dict(re.findall(r'^\s+"([^"]+\.md)":\s+"([^"]+\.(?:md|ipynb))"\s*$', config.read_text(), re.MULTILINE))


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._hidden = 0
        self._heading: tuple[str, list[str]] | None = None
        self.headings: list[tuple[str, str]] = []
        self.media: Counter[str] = Counter()
        self.metadata: dict[str, str] = {}
        self.text: list[str] = []
        self.content_text: list[str] = []
        self.title: list[str] = []
        self._in_title = False
        self._in_article = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {"script", "style", "svg"}:
            self._hidden += 1
        if tag == "title":
            self._in_title = True
        if tag == "article":
            self._in_article += 1
        if self._in_article and tag in {"audio", "embed", "iframe", "img", "object", "video"}:
            self.media[tag] += 1
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and self._in_article:
            self._heading = (values.get("id", ""), [])
        if tag == "link" and values.get("rel") == "canonical" and values.get("href"):
            self.metadata["canonical"] = values["href"]
        if tag == "meta":
            key = values.get("property") or values.get("name")
            if key and values.get("content"):
                self.metadata[key] = values["content"]

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "svg"} and self._hidden:
            self._hidden -= 1
        if tag == "title":
            self._in_title = False
        if tag == "article" and self._in_article:
            self._in_article -= 1
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and self._heading:
            identifier, parts = self._heading
            self.headings.append((identifier, " ".join(" ".join(parts).split())))
            self._heading = None

    def handle_data(self, data: str) -> None:
        if self._hidden:
            return
        normalized = " ".join(data.split())
        if not normalized:
            return
        self.text.append(normalized)
        if self._in_article:
            self.content_text.append(normalized)
        if self._heading:
            self._heading[1].append(normalized)
        if self._in_title:
            self.title.append(normalized)


def parse_page(path: Path) -> PageParser:
    parser = PageParser()
    parser.feed(path.read_text(errors="strict"))
    return parser


def install_version_fixture(site: Path, assets: Path, renderer: str) -> None:
    releases = json.loads((assets / "releases.json").read_text())["releases"]
    versions = [
        {
            "version": "current",
            "title": "Current",
            "aliases": [],
            "properties": {"renderer": renderer, "package_source": "working-tree"},
        }
    ]
    for release in reversed(releases):
        version = release["version"]
        minor = ".".join(version.split(".")[:2])
        aliases = [minor] if not any(entry["version"].startswith(f"{minor}.") for entry in versions) else []
        versions.append({**release, "title": version, "aliases": aliases})
    (site / "versions.json").write_text(json.dumps(versions, indent=2) + "\n")


def assemble_current_site(site: Path, output: Path, assets: Path, renderer: str) -> None:
    """Materialize the same `/current/` topology that Mike publishes."""

    with tempfile.TemporaryDirectory(prefix=f"dspy-{renderer}-deployment-") as directory:
        root = Path(directory)
        repository = root / "repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.name", "parity"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.email", "parity@example.com"], cwd=repository, check=True)
        (repository / "README").write_text("parity fixture\n")
        subprocess.run(["git", "add", "README"], cwd=repository, check=True)
        subprocess.run(["git", "commit", "-qm", "Initial deployment"], cwd=repository, check=True)
        publish_site(
            repository=repository,
            site=site,
            identifier="current",
            title="Current",
            aliases=[],
            renderer=renderer,
            package_source="working-tree",
            mutable=True,
            default=True,
        )

        archive = root / "deployment.tar"
        subprocess.run(
            ["git", "archive", "--format=tar", f"--output={archive}", "versioned-docs"],
            cwd=repository,
            check=True,
        )
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir(parents=True)
        with tarfile.open(archive) as stream:
            stream.extractall(output)

    install_version_fixture(output, assets, renderer)
    inventory = json.loads((output / "versions.json").read_text())
    for entry in inventory:
        version = entry["version"]
        if version == "current":
            continue
        destination = output / version
        destination.mkdir(exist_ok=True)
        (destination / "index.html").write_text(
            f"<!doctype html><html><head><title>DSPy {version}</title></head>"
            f'<body><main data-version="{version}">DSPy {version}</main></body></html>'
        )


def deployment_topology(site: Path, renderer: str) -> dict[str, object]:
    inventory = json.loads((site / "versions.json").read_text())
    current = next((entry for entry in inventory if entry["version"] == "current"), None)
    missing_versions = [
        entry["version"] for entry in inventory if not (site / entry["version"] / "index.html").exists()
    ]
    failures = []
    root_redirect = (site / "index.html").read_text() if (site / "index.html").exists() else ""
    if '"current/" + window.location.search + window.location.hash' not in root_redirect:
        failures.append("root redirect")
    if redirect_target(site, "/api/") != "/current/api/":
        failures.append("deep-link redirect")
    if not current or current.get("properties", {}).get("renderer") != renderer:
        failures.append("Current renderer metadata")
    if missing_versions:
        failures.append("version destinations")
    return {"passed": not failures, "failures": failures, "missing_versions": missing_versions}


def html_pages(site: Path) -> dict[str, Path]:
    return {
        path.relative_to(site).as_posix(): path
        for path in site.rglob("*.html")
        if "assets/images/social" not in path.as_posix()
    }


def search_entries(site: Path) -> list[dict[str, object]]:
    material = site / "search" / "search_index.json"
    path = material if material.exists() else site / "search.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return list(data.get("docs", data.get("items", [])))


def search_content(entries: list[dict[str, object]], notebook_routes: set[str]) -> dict[str, set[str]]:
    """Collect searchable body terms per page despite renderer-specific anchor records."""
    content: dict[str, set[str]] = {}
    for entry in entries:
        location = str(entry["location"])
        page = location.split("#", 1)[0]
        route = f"{page}index.html"
        tokens = content_tokens([str(entry.get("text", ""))], notebook=route in notebook_routes)
        content.setdefault(page, set()).update(tokens)
    return content


def llms_links(site: Path) -> set[str]:
    path = site / "llms.txt"
    if not path.exists():
        return set()
    return set(re.findall(r"\]\((https://dspy\.ai/[^)]+)\)", path.read_text()))


def sitemap_locations(site: Path) -> set[str]:
    sitemap = site / "sitemap.xml"
    if not sitemap.exists():
        return set()
    root = ElementTree.fromstring(sitemap.read_text())
    return {element.text for element in root.iter() if element.tag.endswith("loc") and element.text}


def tab_labels(page: Path) -> set[str]:
    match = re.search(
        r'<nav[^>]+class=(?:"[^"]*md-tabs[^"]*"|[^\s>]*md-tabs[^\s>]*)[^>]*>.*?</nav>',
        page.read_text(),
        re.DOTALL,
    )
    if not match:
        return set()
    parser = PageParser()
    parser.feed(match.group(0))
    return set(parser.text)


def content_tokens(parts: list[str], *, notebook: bool = False) -> Counter[str]:
    renderer_labels = {"assistant", "copied", "out", "response", "system", "user"} if notebook else set()
    tokens: Counter[str] = Counter()
    for text in parts:
        text = re.sub(r"\\(?:u001b|x1b)\[[0-9;]*m", " ", text).replace("\\n", " ")
        tokens.update(
            token.lower()
            for token in re.findall(r"\w+", text)
            if len(token) > 2 and not token.isdecimal() and token.lower() not in renderer_labels
        )
    return tokens


def normalized_heading(text: str) -> str:
    return " ".join(text.replace("¶", "").split())


def redirect_target(site: Path, route: str) -> str | None:
    page = site / route.lstrip("/") / "index.html"
    if not page.exists():
        return None
    canonical = parse_page(page).metadata.get("canonical")
    if not canonical:
        return None
    resolved = urljoin(f"https://dspy.ai{route}", canonical)
    return "/" + resolved.removeprefix("https://dspy.ai/")


def compare_sites(
    material: Path,
    zensical: Path,
    notebook_routes: set[str],
    redirects: dict[str, str],
    stats: dict[str, object],
) -> dict[str, object]:
    material_pages = html_pages(material)
    zensical_pages = html_pages(zensical)
    material_routes = set(material_pages)
    zensical_routes = set(zensical_pages)
    common = material_routes & zensical_routes
    heading_differences = []
    text_differences = []
    metadata_failures = []
    api_symbol_failures = []
    notebook_media_failures = []
    for route in sorted(common):
        left = parse_page(material_pages[route])
        right = parse_page(zensical_pages[route])
        left_headings = {identifier for identifier, _ in left.headings if identifier}
        right_headings = {identifier for identifier, _ in right.headings if identifier}
        missing_headings = left_headings - right_headings
        if missing_headings:
            heading_differences.append(
                {
                    "route": route,
                    "missing": sorted(missing_headings)[:20],
                }
            )
        left_tokens = content_tokens(left.content_text, notebook=route in notebook_routes)
        right_tokens = content_tokens(right.content_text, notebook=route in notebook_routes)
        if route in notebook_routes:
            # Notebook renderers duplicate cell text differently for responsive
            # controls. Presence is meaningful there; multiplicity is not.
            left_tokens = Counter(left_tokens.keys())
            right_tokens = Counter(right_tokens.keys())
        missing_words = left_tokens - right_tokens
        if missing_words:
            text_differences.append(
                {
                    "route": route,
                    "missing_words": [f"{word} ({count})" for word, count in sorted(missing_words.items())[:30]],
                }
            )
        if route in notebook_routes and left.media != right.media:
            notebook_media_failures.append(
                {"route": route, "material": dict(left.media), "zensical": dict(right.media)}
            )
        if "og:image" in left.metadata:
            missing_metadata = [key for key in REQUIRED_METADATA if key not in right.metadata]
            mismatched = {
                key: {"material": left.metadata[key], "zensical": right.metadata.get(key)}
                for key in REQUIRED_METADATA
                # The acknowledged social-card design difference also changes
                # its generated title and image URL, but not metadata coverage.
                if key not in {"og:image", "og:title"}
                if key in left.metadata and left.metadata[key] != right.metadata.get(key)
            }
            if missing_metadata or mismatched:
                metadata_failures.append({"route": route, "missing": missing_metadata, "mismatched": mismatched})
        if route.startswith("api/"):
            material_symbols = {
                identifier: normalized_heading(text)
                for identifier, text in left.headings
                if identifier.startswith("dspy.")
            }
            zensical_symbols = {
                identifier: normalized_heading(text)
                for identifier, text in right.headings
                if identifier.startswith("dspy.")
            }
            missing_symbols = sorted(material_symbols.keys() - zensical_symbols.keys())
            changed_signatures = {
                identifier: {
                    "material": material_symbols[identifier],
                    "zensical": zensical_symbols[identifier],
                }
                for identifier in material_symbols.keys() & zensical_symbols.keys()
                if material_symbols[identifier] != zensical_symbols[identifier]
            }
            if missing_symbols or changed_signatures:
                api_symbol_failures.append(
                    {"route": route, "missing": missing_symbols, "changed_signatures": changed_signatures}
                )

    route_failures = sorted(material_routes - zensical_routes)
    missing_notebooks = sorted(route for route in notebook_routes if route not in zensical_routes)
    redirect_failures = []
    for source, target in redirects.items():
        route = route_for_source(source)
        expected = route_for_source(target.replace(".ipynb", ".md"))
        actual = redirect_target(zensical, route)
        if actual != expected:
            redirect_failures.append({"route": route, "expected": expected, "actual": actual})

    material_search = search_entries(material)
    zensical_search = search_entries(zensical)
    material_locations = {str(entry["location"]) for entry in material_search}
    zensical_locations = {str(entry["location"]) for entry in zensical_search}
    missing_search = sorted(
        location
        for location in material_locations - zensical_locations
        if not ("#" in location and location.split("#", 1)[0] in zensical_locations)
    )
    material_search_content = search_content(material_search, notebook_routes)
    zensical_search_content = search_content(zensical_search, notebook_routes)
    missing_search_content = {
        page: sorted(tokens - zensical_search_content.get(page, set()))[:30]
        for page, tokens in material_search_content.items()
        if tokens - zensical_search_content.get(page, set())
    }
    material_llms = llms_links(material)
    zensical_llms = llms_links(zensical)
    material_sitemap = sitemap_locations(material)
    zensical_sitemap = sitemap_locations(zensical)

    asset_failures = []
    for asset in (
        "stylesheets/extra.css",
        "js/runllm-widget.js",
        "js/tutorial-nav.js",
        "js/hero-interactive.js",
    ):
        left = material / asset
        right = zensical / asset
        if not right.exists() or not left.exists() or left.read_bytes() != right.read_bytes():
            asset_failures.append(asset)

    home = (zensical / "index.html").read_text()
    template_markers = (
        'class="hp-hero',
        'data-md-component="search"',
        'data-md-color-scheme="default"',
        'data-md-color-scheme="slate"',
        'data-md-type="navigation"',
    )
    missing_template_markers = [marker for marker in template_markers if marker not in home]
    missing_stats = [str(value) for key, value in stats.items() if key != "release_date" and str(value) not in home]
    selector_failures = [] if re.search(r'"provider"\s*:\s*"mike"', home) else ["index.html"]
    required_tabs = {
        "Overview",
        "Getting Started",
        "Diving Deeper",
        "Tutorials",
        "API Reference",
        "Community",
        "FAQ",
    }
    feature_failures = []
    missing_tabs = sorted(required_tabs - tab_labels(zensical / "index.html"))
    if missing_tabs:
        feature_failures.append({"feature": "primary navigation tabs", "missing": missing_tabs})
    home_title = " ".join(parse_page(zensical / "index.html").title)
    if home_title != "DSPy":
        feature_failures.append({"feature": "home document title", "expected": "DSPy", "actual": home_title})
    missing_sitemap = sorted(material_sitemap - zensical_sitemap)
    if missing_sitemap:
        feature_failures.append({"feature": "sitemap", "missing": missing_sitemap})

    social_failures = []
    social_visual_differences = []
    social_images = []
    from PIL import Image, ImageChops

    for route, page in zensical_pages.items():
        parsed = parse_page(page)
        image_url = parsed.metadata.get("og:image")
        if not image_url:
            continue
        image_path = zensical / image_url.removeprefix("https://dspy.ai/")
        if not image_path.exists():
            social_failures.append({"route": route, "error": "missing image"})
            continue
        social_images.append(image_url)
        with Image.open(image_path) as image:
            if image.size != (1200, 630):
                social_failures.append({"route": route, "error": f"size is {image.size}"})
                continue
        material_image_url = parse_page(material_pages[route]).metadata.get("og:image")
        if material_image_url and route in {
            "index.html",
            "getting-started/first-program/index.html",
            "api/models/LM/index.html",
            "tutorials/rag/index.html",
        }:
            material_image_path = material / material_image_url.removeprefix("https://dspy.ai/")
            with Image.open(material_image_path) as material_image, Image.open(image_path) as zensical_image:
                difference = ImageChops.difference(material_image.convert("RGB"), zensical_image.convert("RGB"))
            pixels = list(difference.get_flattened_data())
            changed = sum(max(pixel) > 24 for pixel in pixels) / len(pixels)
            mean = sum(sum(pixel) for pixel in pixels) / (len(pixels) * 3)
            if changed > 0.05 or mean > 5:
                social_visual_differences.append(
                    {
                        "route": route,
                        "error": "visual difference",
                        "changed_pixel_ratio": round(changed, 6),
                        "mean_channel_difference": round(mean, 3),
                    }
                )

    if len(social_images) != len(set(social_images)):
        social_failures.append({"error": "social-card images are not page-specific"})

    extra_routes = sorted(zensical_routes - material_routes)
    return {
        "routes": {
            "passed": not route_failures and not extra_routes,
            "material_count": len(material_routes),
            "zensical_count": len(zensical_routes),
            "missing": route_failures,
            "extra": extra_routes,
        },
        "content": {
            "passed": not heading_differences and not text_differences,
            "heading_failures": heading_differences,
            "text_failures": text_differences,
        },
        "api": {"passed": not api_symbol_failures, "failures": api_symbol_failures},
        "notebooks": {
            "passed": not missing_notebooks and not notebook_media_failures,
            "missing": missing_notebooks,
            "media_failures": notebook_media_failures,
        },
        "redirects": {"passed": not redirect_failures, "failures": redirect_failures},
        "metadata": {"passed": not metadata_failures, "failures": metadata_failures},
        "social_cards": {
            "passed": not social_failures,
            "failures": social_failures,
            "visual_differences": social_visual_differences,
        },
        "llms": {
            "passed": bool(material_llms) and not (material_llms - zensical_llms),
            "material_count": len(material_llms),
            "zensical_count": len(zensical_llms),
            "missing": sorted(material_llms - zensical_llms),
            "extra": sorted(zensical_llms - material_llms),
        },
        "search": {
            "passed": bool(material_search) and not missing_search and not missing_search_content,
            "material_count": len(material_search),
            "zensical_count": len(zensical_search),
            "missing_locations": missing_search,
            "missing_content": missing_search_content,
        },
        "assets": {"passed": not asset_failures, "failures": asset_failures},
        "templates": {"passed": not missing_template_markers, "missing": missing_template_markers},
        "stats": {"passed": not missing_stats, "missing_values": missing_stats},
        "version_selector": {"passed": not selector_failures, "missing": selector_failures},
        "features": {"passed": not feature_failures, "failures": feature_failures},
    }


def passed(report: dict[str, object]) -> bool:
    sections = [value for value in report.values() if isinstance(value, dict) and "passed" in value]
    return bool(sections) and all(section["passed"] for section in sections)


def write_report(report: dict[str, object], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"passed": passed(report), **report}, indent=2) + "\n")


def build_site(python: Path, renderer: str, project: Path, output: Path) -> None:
    module = "mkdocs" if renderer == "material" else "zensical"
    if renderer == "material":
        arguments = [
            python,
            "-m",
            module,
            "build",
            "--clean",
            "--config-file",
            project / "mkdocs.yml",
            "--site-dir",
            output,
        ]
    else:
        arguments = [python, "-m", module, "build", "--clean", "--config-file", project / "mkdocs.yml"]
    subprocess.run([str(argument) for argument in arguments], cwd=project, check=True)
    if renderer == "zensical":
        shutil.move(project / "site", output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path("docs"))
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--material-output", type=Path, default=Path("site-material"))
    parser.add_argument("--zensical-output", type=Path, default=Path("site-zensical"))
    parser.add_argument("--report", type=Path, default=Path("zensical-parity.json"))
    parser.add_argument("--keep-projects", type=Path)
    parser.add_argument("--no-fail", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = args.project.resolve()
    repository = source.parent
    material_output = args.material_output.resolve()
    zensical_output = args.zensical_output.resolve()
    for output in (material_output, zensical_output):
        shutil.rmtree(output, ignore_errors=True)

    context = tempfile.TemporaryDirectory(prefix="dspy-zensical-parity-") if not args.keep_projects else None
    root = Path(context.name) if context else args.keep_projects.resolve()
    root.mkdir(parents=True, exist_ok=True)
    material_project = root / "material"
    zensical_project = root / "zensical"
    material_site = root / "material-site"
    zensical_site = root / "zensical-site"
    shutil.rmtree(material_project, ignore_errors=True)
    shutil.rmtree(zensical_project, ignore_errors=True)
    copy_project(source, material_project)

    stats = frozen_stats(source)
    freeze_material_config(material_project / "mkdocs.yml", stats, repository)
    redirects = redirect_maps(source / "mkdocs.yml")

    build_site(args.python, "material", material_project, material_site)
    notebooks = build_zensical_site(
        config=source / "mkdocs.yml",
        output=zensical_site,
        python=args.python,
        stats=stats,
        keep_project=zensical_project,
    )
    for site in (material_site, zensical_site):
        install_shared_header_styles(site, source / "versioning" / "header.css")
        optimize_site(site)

    report = compare_sites(material_site, zensical_site, notebooks, redirects, stats)
    assemble_current_site(material_site, material_output, source / "versioning", "material")
    assemble_current_site(zensical_site, zensical_output, source / "versioning", "zensical")
    report["deployment_topology"] = {
        "passed": True,
        "material": deployment_topology(material_output, "material"),
        "zensical": deployment_topology(zensical_output, "zensical"),
    }
    report["deployment_topology"]["passed"] = all(
        result["passed"] for key, result in report["deployment_topology"].items() if key != "passed"
    )
    report["frozen_stats"] = stats
    write_report(report, args.report.resolve())
    if context:
        context.cleanup()
    if not passed(report) and not args.no_fail:
        raise SystemExit(f"Zensical parity failed; see {args.report}")


if __name__ == "__main__":
    main()
