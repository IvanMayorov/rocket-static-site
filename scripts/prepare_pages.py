#!/usr/bin/env python3
"""Prepare a Webflow export for project Pages with clean, canonical URLs."""

from __future__ import annotations

import argparse
import posixpath
import re
import shutil
from html import escape
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


SKIP_NAMES = {".git", ".github", "scripts", "_site"}
HTML_URL_ATTRS = re.compile(
    r"(?P<attr>\b(?:href|src|action|poster|content|data-src|data-href|data-background|xlink:href)\s*=\s*)"
    r"(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
SRCSET_ATTR = re.compile(r"(\bsrcset\s*=\s*)([\"'])(.*?)(\2)", re.IGNORECASE | re.DOTALL)
STYLE_ATTR = re.compile(r"(\bstyle\s*=\s*)([\"'])(.*?)(\2)", re.IGNORECASE | re.DOTALL)
STYLE_BLOCK = re.compile(r"(<style\b[^>]*>)(.*?)(</style\s*>)", re.IGNORECASE | re.DOTALL)
CSS_URL = re.compile(r"url\(\s*([\"']?)(.*?)\1\s*\)", re.IGNORECASE)
LINK_TAG = re.compile(r"<link\b[^>]*>", re.IGNORECASE | re.DOTALL)
ATTR_VALUE = re.compile(r"\b(?P<name>[\w:-]+)\s*=\s*(?P<quote>[\"'])(?P<value>.*?)(?P=quote)", re.DOTALL)


def prefix_url(path: str, base: str) -> str:
    if path.startswith("/") and not path.startswith("//"):
        if path == base or path.startswith(base + "/"):
            return path
        return base + path
    return path


def page_url(relative_path: Path) -> str:
    """Return the source URL represented by an exported HTML file."""
    if relative_path.name.lower() in {"index.html", "index.htm"}:
        parent = relative_path.parent.as_posix()
        return "/" if parent == "." else "/" + parent.strip("/") + "/"
    return "/" + relative_path.as_posix()


def clean_route(value: str, base: str) -> str | None:
    """Convert a same-site page URL into its directory-index route."""
    parsed = urlsplit(value.strip())
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return None

    path = parsed.path
    if path == base or path.startswith(base + "/"):
        path = path[len(base):] or "/"
    if path.lower().endswith((".html", ".htm")):
        path = path.rsplit(".", 1)[0]
    path = posixpath.normpath(path)
    if not path.startswith("/"):
        path = "/" + path
    return "/" if path == "/" else path.rstrip("/") + "/"


def get_canonical(content: str) -> str | None:
    for match in LINK_TAG.finditer(content):
        tag = match.group(0)
        attributes = {m.group("name").lower(): m.group("value") for m in ATTR_VALUE.finditer(tag)}
        if "canonical" in attributes.get("rel", "").lower().split():
            return attributes.get("href")
    return None


def route_exists(target: Path, route: str) -> bool:
    if route == "/":
        return (target / "index.html").is_file()
    return (target / route.strip("/") / "index.html").is_file()


def route_can_be_created(target: Path, route: str) -> bool:
    if route_exists(target, route):
        return False
    current = target
    for part in route.strip("/").split("/"):
        if not part:
            continue
        current /= part
        if current.exists() and not current.is_dir():
            return False
    return True


def resolve_local_url(value: str, context_url: str, base: str, path_map: dict[str, str], routes: set[str]) -> str:
    """Resolve a local URL from the original page location and add the Pages base."""
    original = value.strip()
    parsed = urlsplit(original)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return original

    if parsed.path.startswith("/"):
        source_path = posixpath.normpath(parsed.path)
    else:
        context_dir = context_url if context_url.endswith("/") else posixpath.dirname(context_url)
        source_path = posixpath.normpath(posixpath.join(context_dir, parsed.path))
    if not source_path.startswith("/"):
        source_path = "/" + source_path

    destination = path_map.get(source_path)
    if destination is None and source_path.lower().endswith(("/index.html", "/index.htm")):
        destination = clean_route(source_path, base)
    if destination is None and source_path.rstrip("/") + "/" in routes:
        destination = source_path.rstrip("/") + "/"
    if destination is None:
        destination = source_path

    return urlunsplit(("", "", prefix_url(destination, base), parsed.query, parsed.fragment))


def rewrite_srcset(value: str, context_url: str, base: str) -> str:
    candidates = []
    for candidate in value.split(","):
        parts = candidate.strip().split(None, 1)
        if parts:
            parts[0] = resolve_local_url(parts[0], context_url, base, {}, set())
            candidates.append(" ".join(parts))
        else:
            candidates.append(candidate)
    return ", ".join(candidates)


def rewrite_css(value: str, context_url: str, base: str) -> str:
    return CSS_URL.sub(
        lambda match: "url(" + match.group(1)
        + resolve_local_url(match.group(2), context_url, base, {}, set())
        + match.group(1) + ")",
        value,
    )


def rewrite_canonical_tags(content: str, canonical_url: str) -> str:
    found = False

    def replace_tag(match: re.Match[str]) -> str:
        nonlocal found
        tag = match.group(0)
        attributes = {m.group("name").lower(): m.group("value") for m in ATTR_VALUE.finditer(tag)}
        if "canonical" not in attributes.get("rel", "").lower().split():
            return tag
        if found:
            return ""
        found = True
        if re.search(r"\bhref\s*=", tag, re.IGNORECASE):
            return re.sub(
                r"(?P<prefix>\bhref\s*=\s*)(?P<quote>[\"']).*?(?P=quote)",
                lambda href: href.group("prefix") + href.group("quote")
                + escape(canonical_url, quote=True) + href.group("quote"),
                tag,
                count=1,
                flags=re.IGNORECASE | re.DOTALL,
            )
        return tag[:-1] + ' href="' + escape(canonical_url, quote=True) + '">'

    content = LINK_TAG.sub(replace_tag, content)
    if not found:
        head = re.search(r"<head\b[^>]*>", content, re.IGNORECASE)
        if head:
            tag = '<link rel="canonical" href="' + escape(canonical_url, quote=True) + '">'
            content = content[:head.end()] + "\n    " + tag + content[head.end():]
    return content


def rewrite_html(
    content: str,
    base: str,
    context_url: str,
    canonical_url: str,
    path_map: dict[str, str],
    routes: set[str],
) -> str:
    def replace_url_attr(match: re.Match[str]) -> str:
        attr_name = match.group("attr").split("=", 1)[0].strip().lower()
        value = match.group("value")
        # A meta description is prose; only treat content attributes as URLs when root-relative.
        if attr_name == "content" and not value.strip().startswith("/"):
            return match.group(0)
        rewritten = resolve_local_url(value, context_url, base, path_map, routes)
        return match.group("attr") + match.group("quote") + rewritten + match.group("quote")

    content = HTML_URL_ATTRS.sub(replace_url_attr, content)
    content = SRCSET_ATTR.sub(
        lambda match: match.group(1) + match.group(2)
        + rewrite_srcset(match.group(3), context_url, base) + match.group(4),
        content,
    )
    content = STYLE_ATTR.sub(
        lambda match: match.group(1) + match.group(2)
        + rewrite_css(match.group(3), context_url, base) + match.group(4),
        content,
    )
    content = STYLE_BLOCK.sub(
        lambda match: match.group(1) + rewrite_css(match.group(2), context_url, base) + match.group(3),
        content,
    )
    return rewrite_canonical_tags(content, canonical_url)


def copy_source(source: Path, target: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = set(names) & SKIP_NAMES
        if Path(directory) == source:
            ignored |= {"README.md", ".nojekyll"}
        return ignored

    shutil.copytree(source, target, ignore=ignore)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="Pages project base path, such as /rocket-static-site")
    parser.add_argument("--site-url", required=True, help="Public project URL, such as https://owner.github.io/repository")
    parser.add_argument("--output", default="_site")
    args = parser.parse_args()

    base = "/" + args.base.strip("/")
    site_url = args.site_url.rstrip("/")
    source = Path.cwd().resolve()
    target = Path(args.output).resolve()
    if target == source or source not in target.parents:
        raise SystemExit("Output must be a child directory of the repository")
    if target.exists():
        shutil.rmtree(target)
    copy_source(source, target)

    pages: dict[Path, dict[str, str | None]] = {}
    html_files = sorted(
        path.relative_to(source)
        for path in source.rglob("*")
        if path.is_file() and path.suffix.lower() in {".html", ".htm"}
        and not set(path.relative_to(source).parts) & SKIP_NAMES
    )
    existing_routes = {
        page_url(path)
        for path in html_files
        if path.name.lower() in {"index.html", "index.htm"}
    }
    for relative_path in html_files:
        original = (source / relative_path).read_text(encoding="utf-8")
        raw_canonical = get_canonical(original)
        candidate_route = clean_route(raw_canonical, base) if raw_canonical else None
        derived_route = clean_route("/" + relative_path.with_suffix("").as_posix(), base)
        pages[relative_path] = {
            "context": page_url(relative_path),
            "canonical": raw_canonical,
            "candidate": candidate_route,
            "derived": derived_route,
        }

    # Routes that can be generated safely, before handling canonical links that target them.
    available_routes = set(existing_routes)
    for relative_path, page in pages.items():
        if relative_path.name.lower() in {"index.html", "index.htm", "404.html", "401.html"}:
            continue
        for route in (page["candidate"], page["derived"]):
            if route and route_can_be_created(target, route):
                available_routes.add(route)

    alias_sources: dict[str, Path] = {}
    for relative_path, page in pages.items():
        if relative_path.name.lower() in {"404.html", "401.html"}:
            continue
        candidate = page["candidate"]
        derived = page["derived"]
        route = candidate if candidate in available_routes else derived if derived in available_routes else None
        if route and route not in existing_routes:
            alias_sources.setdefault(route, relative_path)

    path_map: dict[str, str] = {}
    canonical_routes: dict[Path, str] = {}

    for relative_path, page in pages.items():
        source_url = page["context"]
        if relative_path.name.lower() in {"index.html", "index.htm"}:
            own_route = source_url
            candidate = page["candidate"]
            canonical_route = candidate if candidate in available_routes else own_route
            canonical_routes[relative_path] = canonical_route
            path_map[source_url] = canonical_route
            path_map["/" + relative_path.as_posix()] = canonical_route
            continue

        candidate = page["candidate"]
        derived = page["derived"]
        if candidate in existing_routes:
            route = candidate
        elif candidate in available_routes:
            route = candidate
        elif derived in available_routes:
            route = derived
        else:
            route = None

        if route:
            path_map[source_url] = route
            canonical_routes[relative_path] = route
            if route != derived and candidate in available_routes:
                canonical_routes[relative_path] = candidate
        path_map["/" + relative_path.as_posix()] = route or source_url

    for route, relative_path in alias_sources.items():
        destination = target / route.strip("/") / "index.html"
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target / relative_path, destination)

    routes = existing_routes | set(alias_sources)
    output_contexts: dict[Path, tuple[Path, str]] = {}
    for relative_path, page in pages.items():
        context_url = page["context"]
        output_contexts[relative_path] = (relative_path, context_url)
    for route, relative_path in alias_sources.items():
        alias_path = Path(route.strip("/")) / "index.html"
        output_contexts[alias_path] = (relative_path, pages[relative_path]["context"])

    for output_path, (source_relative_path, context_url) in output_contexts.items():
        path = target / output_path
        content = path.read_text(encoding="utf-8")
        raw_canonical = pages[source_relative_path]["canonical"]
        candidate = clean_route(raw_canonical, base) if raw_canonical else None
        canonical_route = candidate if candidate in routes else canonical_routes.get(source_relative_path)
        if not canonical_route:
            canonical_route = page_url(source_relative_path)
        if raw_canonical and urlsplit(raw_canonical).scheme:
            canonical_url = raw_canonical
        else:
            canonical_url = site_url + canonical_route
        content = rewrite_html(
            content,
            base,
            context_url,
            canonical_url,
            path_map,
            routes,
        )
        path.write_text(content, encoding="utf-8")

    for path in target.rglob("*.css"):
        relative_path = path.relative_to(target).as_posix()
        content = path.read_text(encoding="utf-8")
        content = rewrite_css(content, "/" + relative_path, base)
        path.write_text(content, encoding="utf-8")

    sitemap_urls = {
        site_url + canonical_route
        for relative_path, canonical_route in canonical_routes.items()
        if relative_path.name.lower() not in {"404.html", "401.html"}
        and canonical_route in routes
        and not re.search(
            r'<meta\b(?=[^>]*\bname=["\']robots["\'])(?=[^>]*\bcontent=["\'][^"\']*noindex)',
            (source / relative_path).read_text(encoding="utf-8"),
            re.IGNORECASE | re.DOTALL,
        )
    }
    sitemap = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    sitemap.extend("  <url><loc>" + escape(url) + "</loc></url>" for url in sorted(sitemap_urls))
    sitemap.append("</urlset>")
    (target / "sitemap.xml").write_text("\n".join(sitemap) + "\n", encoding="utf-8")
    (target / ".nojekyll").touch()


if __name__ == "__main__":
    main()
