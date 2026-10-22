"""Build crawlable documentation and verify internal links without network access."""

import argparse
import html
import json
import posixpath
import re
import shutil
import subprocess
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from xml.etree.ElementTree import Element, SubElement, tostring

from markdown_it import MarkdownIt

from atlas_terminal import __version__
from atlas_terminal.documentation import SITE_ORIGIN as DOCUMENTATION_ORIGIN

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/andoseiryu-atlassupport/atlas-terminal"
ROOT_PAGES = (
    "README.md",
    "DATA_SOURCES.md",
    "IMPLEMENTATION_STATUS.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "DEPENDENCY_AUDIT.md",
    "THIRD_PARTY_NOTICES.md",
)


def output_path(source: str) -> str:
    return "index.html" if source == "docs/index.md" else str(Path(source).with_suffix(".html"))


def slug(text: str) -> str:
    return re.sub(r"[^\w -]", "", text.lower()).replace(" ", "-")


def revised(source: str) -> str:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%cs", "--", source],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip() or datetime.now(UTC).date().isoformat()


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.add(values["id"])
        for name in ("href", "src"):
            if name in values:
                self.targets.append(values[name])


def verify(directory: Path) -> None:
    documents = {}
    for path in directory.rglob("*.html"):
        parser = Links()
        parser.feed(path.read_text())
        documents[path] = parser
    failures = []
    for path, links in documents.items():
        for raw in links.targets:
            target = urlsplit(raw)
            if target.scheme or target.netloc:
                continue
            destination = (path.parent / unquote(target.path)).resolve() if target.path else path
            if destination.is_dir():
                destination /= "index.html"
            if not destination.is_relative_to(directory.resolve()) or not destination.is_file():
                failures.append(f"{path.relative_to(directory)}: missing {raw}")
            elif target.fragment and destination in documents:
                if unquote(target.fragment) not in documents[destination].ids:
                    failures.append(f"{path.relative_to(directory)}: missing anchor {raw}")
    if failures:
        raise ValueError("\n".join(failures))


def build(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    sources = [*ROOT_PAGES, *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs").glob("*.md"))]
    pages = {source: output_path(source) for source in sources}
    nav_sources = (
        ("docs/index.md", "Home"),
        ("docs/quickstart.md", "Quickstart"),
        ("docs/codex.md", "Codex"),
        ("docs/use-cases.md", "Examples"),
        ("docs/presets.md", "Presets"),
        ("DATA_SOURCES.md", "Sources"),
        ("docs/methodology.md", "Methodology"),
        ("docs/faq.md", "FAQ"),
    )
    sitemap = Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    llms = [
        "# Atlas Terminal",
        "",
        "> Free company financials, US macro and reference FX through one MCP connection.",
        "",
        "Current snapshots are not PIT history or real-time prices. Fixtures are fictional.",
        "Cite original publishers for numerical facts; Atlas documents selection and calculations.",
        "",
        "## Documentation",
        "",
    ]
    for source, output in pages.items():
        text = (ROOT / source).read_text()
        parser = MarkdownIt("commonmark", {"html": False}).enable("table")
        visible_text = re.sub(r"<!--\s*mcp-name:.*?-->", "", text)
        tokens = parser.parse(visible_text)
        heading_counts = {}
        title = next((line[2:] for line in text.splitlines() if line.startswith("# ")), source)
        paragraphs = re.split(r"\n\s*\n", visible_text)
        description = next(
            (p for p in paragraphs if p and not p.startswith(("#", "```", "|", "---"))), title
        )
        description = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", description)
        description = re.sub(r"[*`\n]", "", description)[:240]

        def rewrite(raw: str, source: str = source, output: str = output) -> str:
            target = urlsplit(raw)
            if target.scheme or target.netloc or not target.path:
                return raw
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname(source), unquote(target.path))
            )
            suffix = ("?" + target.query if target.query else "") + (
                "#" + target.fragment if target.fragment else ""
            )
            if resolved in pages:
                return posixpath.relpath(pages[resolved], posixpath.dirname(output) or ".") + suffix
            if resolved.startswith(("schemas/", "examples/")) or resolved == "LICENSE":
                src = ROOT / resolved
                if src.is_file() and src.resolve().is_relative_to(ROOT):
                    dest = directory / resolved
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, dest)
                    return posixpath.relpath(resolved, posixpath.dirname(output) or ".") + suffix
            return REPO + "/blob/main/" + quote(resolved, safe="/") + suffix

        for index, token in enumerate(tokens):
            if token.type == "heading_open":
                identity = slug(tokens[index + 1].content)
                count = heading_counts.get(identity, 0)
                heading_counts[identity] = count + 1
                token.attrSet("id", identity + (f"-{count}" if count else ""))
            for child in token.children or []:
                if child.type == "link_open":
                    child.attrSet("href", rewrite(child.attrGet("href") or ""))
                elif child.type == "image":
                    child.attrSet("src", rewrite(child.attrGet("src") or ""))
        content = parser.renderer.render(tokens, parser.options, {})
        canonical = DOCUMENTATION_ORIGIN + "/" + ("" if output == "index.html" else output)
        nav = " ".join(
            '<a href="'
            + posixpath.relpath(pages[p], posixpath.dirname(output) or ".")
            + '">'
            + label
            + "</a>"
            for p, label in nav_sources
        )
        date = revised(source)
        metadata = {
            "@context": "https://schema.org",
            "@type": "TechArticle",
            "headline": title,
            "description": description,
            "url": canonical,
            "dateModified": date,
            "inLanguage": "en",
            "isPartOf": {
                "@type": "WebSite",
                "name": "Atlas Terminal",
                "url": DOCUMENTATION_ORIGIN + "/",
            },
        }
        if output == "index.html":
            metadata.update(
                {
                    "@type": "SoftwareSourceCode",
                    "name": "Atlas Terminal",
                    "codeRepository": REPO,
                    "programmingLanguage": "Python",
                    "version": __version__,
                    "license": "https://www.apache.org/licenses/LICENSE-2.0",
                }
            )
        css = posixpath.relpath("assets/docs.css", posixpath.dirname(output) or ".")
        markdown = posixpath.relpath(source, posixpath.dirname(output) or ".")
        rendered = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} | Atlas Terminal</title>
<meta name="description" content="{html.escape(description, quote=True)}">
<link rel="canonical" href="{canonical}"><meta name="robots" content="index,follow">
<meta property="og:title" content="{html.escape(title, quote=True)}">
<meta property="og:description" content="{html.escape(description, quote=True)}">
<meta property="og:url" content="{canonical}"><meta property="og:type" content="article">
<link rel="stylesheet" href="{css}"><link rel="alternate" type="text/markdown" href="{markdown}">
<script type="application/ld+json">{json.dumps(metadata).replace("<", "\\u003c")}</script></head>
<body><a class="skip" href="#content">Skip to content</a>
<header><nav aria-label="Documentation">{nav}</nav></header>
<main id="content"><article>{content}</article></main>
<footer>Atlas Terminal {__version__} · Documentation revision: {date} ·
<a href="{markdown}">Markdown</a> ·
<a href="{REPO}/blob/main/{quote(source, safe="/")}">Source</a> ·
<a href="{REPO}/issues">Support</a></footer></body></html>'''
        path = directory / output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered)
        raw_path = directory / source
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(text)
        node = SubElement(sitemap, "url")
        SubElement(node, "loc").text = canonical
        SubElement(node, "lastmod").text = date
        llms.append(f"- [{title}]({canonical}): [Markdown]({DOCUMENTATION_ORIGIN}/{source})")
    (directory / "assets").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "docs/assets/docs.css", directory / "assets/docs.css")
    (directory / "sitemap.xml").write_bytes(
        tostring(sitemap, encoding="utf-8", xml_declaration=True)
    )
    (directory / "llms.txt").write_text("\n".join(llms) + "\n")
    (directory / "robots.txt").write_text(
        "User-agent: OAI-SearchBot\nAllow: /\n\nUser-agent: *\nAllow: /\n\nSitemap: "
        + DOCUMENTATION_ORIGIN
        + "/sitemap.xml\n"
    )
    # GitHub project Pages cannot override the account domain's root robots.txt.
    # This project-level file documents intent; check the root file after deployment.
    (directory / ".nojekyll").touch()
    (directory / "404.html").write_text(
        '<!doctype html><html lang="en"><head><meta name="robots" content="noindex">'
        "<title>Page not found | Atlas Terminal</title></head><body><h1>Page not found</h1>"
        f'<p><a href="{DOCUMENTATION_ORIGIN}/">Atlas Terminal documentation</a></p></body></html>'
    )
    verify(directory.resolve())
    print(
        json.dumps(
            {
                "status": "PASS",
                "pages": len(pages),
                "internal_links": "verified",
                "output": str(directory),
            }
        )
    )


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, default=ROOT / "build/site")
    args = cli.parse_args()
    build(args.output.resolve())
