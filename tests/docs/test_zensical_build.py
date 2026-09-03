import gzip
import json
from pathlib import Path

import pytest

from docs.hooks import fetch_stats as stats_hook
from docs.scripts.build_docs import install_shared_header_styles
from docs.scripts.zensical_build import (
    add_missing_titles,
    convert_notebooks,
    generate_llms,
    load_config,
    nav_entries,
    navigation_titles,
    prepare_config,
    route_for_source,
    write_redirects,
    write_sitemap,
)


def test_zensical_configuration_is_native_and_keeps_build_compatibility_separate():
    project = Path(__file__).parents[2] / "docs"
    config = load_config(project / "mkdocs.yml")
    build = load_config(project / "build.yml")

    assert config["theme"]["variant"] == "classic"
    assert "name" not in config["theme"]
    assert [plugin if isinstance(plugin, str) else next(iter(plugin)) for plugin in config["plugins"]] == [
        "search",
        "mkdocstrings",
    ]
    assert "hooks" not in config
    assert build["redirects"]["intro/index.md"] == "index.md"
    assert "Tutorials - Notebooks" in build["llms"]["sections"]


def test_prepared_config_adds_only_runtime_values():
    config = {"site_name": "DSPy", "extra": {"social": []}}

    result = prepare_config(config, {"stars": "10k"})

    assert result == {
        "site_name": "DSPy",
        "site_dir": "site",
        "extra": {"social": [], "stats": {"stars": "10k"}},
    }
    assert config == {"site_name": "DSPy", "extra": {"social": []}}


def test_shared_header_styles_use_page_relative_links(tmp_path):
    site = tmp_path / "site"
    nested = site / "guide" / "example"
    nested.mkdir(parents=True)
    (site / "index.html").write_text("<html><head></head><body>Home</body></html>")
    (nested / "index.html").write_text("<html><head></head><body>Guide</body></html>")
    source = tmp_path / "header.css"
    source.write_text(".md-version { width: 10rem; }\n")

    install_shared_header_styles(site, source)

    assert '<link rel="stylesheet" href="_static/dspy-header.css">' in (site / "index.html").read_text()
    assert '<link rel="stylesheet" href="../../_static/dspy-header.css">' in (nested / "index.html").read_text()
    assert (site / "_static" / "dspy-header.css").read_text() == source.read_text()


def test_extracts_navigation_titles(tmp_path):
    config = tmp_path / "mkdocs.yml"
    config.write_text(
        "nav:\n    - Overview: index.md\n    - Retrieval-Augmented Generation (RAG): tutorials/rag/index.md\n"
    )

    parsed = load_config(config)

    assert navigation_titles(parsed) == {
        "/": "DSPy",
        "/tutorials/rag/": "Retrieval-Augmented Generation (RAG)",
    }


def test_adds_navigation_title_only_when_a_page_has_no_heading(tmp_path):
    docs = tmp_path / "docs"
    (docs / "tutorials" / "classification").mkdir(parents=True)
    missing = docs / "tutorials" / "classification" / "index.md"
    missing.write_text("Page content\n")
    existing = docs / "guide.md"
    existing.write_text("# Existing\n")
    config = tmp_path / "mkdocs.yml"
    config.write_text("nav:\n    - Classification: tutorials/classification/index.md\n    - Guide: guide.md\n")

    add_missing_titles(docs, nav_entries(load_config(config)["nav"]))

    assert missing.read_text().startswith("# Classification\n\n")
    assert existing.read_text() == "# Existing\n"


def test_routes_and_redirects_preserve_nested_targets(tmp_path):
    site = tmp_path / "site"
    site.mkdir()

    write_redirects(site, {"intro/index.md": "index.md", "old/guide.md": "tutorials/rag/index.md"})

    assert route_for_source("index.md") == "/"
    assert route_for_source("guides/index.md") == "/guides/"
    assert route_for_source("guides/setup.md") == "/guides/setup/"
    assert '<link rel="canonical" href="../">' in (site / "intro" / "index.html").read_text()
    assert '<link rel="canonical" href="../../tutorials/rag/">' in (site / "old" / "guide" / "index.html").read_text()


def test_notebook_conversion_preserves_python_pages(tmp_path):
    nbformat = pytest.importorskip("nbformat")
    notebook = nbformat.v4.new_notebook(cells=[nbformat.v4.new_markdown_cell("# Guide")])
    nbformat.write(notebook, tmp_path / "guide.ipynb")
    helper = tmp_path / "helper.py"
    helper.write_text("VALUE = 1\n")

    routes = convert_notebooks(tmp_path)

    assert routes == {"guide/index.html", "helper/index.html"}
    assert (tmp_path / "guide.md").exists()
    assert helper.exists()
    assert "```python\nVALUE = 1" in (tmp_path / "helper.md").read_text()


def test_llms_generation_uses_the_configured_inventory(tmp_path):
    docs = tmp_path / "docs"
    (docs / "guides").mkdir(parents=True)
    (docs / "index.md").write_text("# DSPy\n")
    (docs / "guides" / "first.md").write_text("# First Guide\n")
    (docs / "excluded.md").write_text("# Excluded\n")
    output = tmp_path / "llms.txt"

    generate_llms(
        docs,
        output,
        "https://dspy.ai/",
        {
            "markdown_description": "Description",
            "sections": {
                "Home": [{"index.md": "Overview"}],
                "Guides": ["guides/**.md"],
            },
        },
    )

    result = output.read_text()
    assert "Description" in result
    assert "[DSPy](https://dspy.ai/index.md): Overview" in result
    assert "[First Guide](https://dspy.ai/guides/first/index.md)" in result
    assert "Excluded" not in result


def test_sitemap_uses_source_routes_and_writes_matching_gzip(tmp_path):
    docs = tmp_path / "docs"
    (docs / "guide").mkdir(parents=True)
    (docs / "index.md").write_text("# Home\n")
    (docs / "guide" / "index.ipynb").write_text("{}\n")
    (docs / "example.py").write_text("VALUE = 1\n")
    site = tmp_path / "site"
    site.mkdir()

    write_sitemap(site, "https://dspy.ai/", docs)

    sitemap = (site / "sitemap.xml").read_text()
    assert "<loc>https://dspy.ai/</loc>" in sitemap
    assert "<loc>https://dspy.ai/guide/</loc>" in sitemap
    assert "<loc>https://dspy.ai/example/</loc>" in sitemap
    assert gzip.decompress((site / "sitemap.xml.gz").read_bytes()).decode() == sitemap


def test_public_stats_api_does_not_leak_cache_metadata(tmp_path, monkeypatch):
    cache = tmp_path / "stats.json"
    fetched = {"stars": "10k"}
    monkeypatch.setattr(stats_hook, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(stats_hook, "CACHE_FILE", cache)
    monkeypatch.setattr(stats_hook, "_fetch_all", lambda: fetched)

    result = stats_hook.fetch_stats()

    assert result == {"stars": "10k"}
    assert fetched == {"stars": "10k"}
    assert stats_hook.fetch_stats() == {"stars": "10k"}
    assert set(json.loads(cache.read_text())) == {"stars", "_ts", "_cache_version"}
