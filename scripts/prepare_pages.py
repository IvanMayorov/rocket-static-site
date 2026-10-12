#!/usr/bin/env python3
"""Copy the static site and prefix root-relative URLs for a project Pages URL."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path


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


def prefix_url(value: str, base: str) -> str:
    value = value.strip()
    if value.startswith("/") and not value.startswith("//"):
        if value == base or value.startswith(base + "/"):
            return value
        return base + value
    return value


def rewrite_srcset(value: str, base: str) -> str:
    candidates = []
    for candidate in value.split(","):
        parts = candidate.strip().split(None, 1)
        if parts:
            parts[0] = prefix_url(parts[0], base)
            candidates.append(" ".join(parts))
        else:
            candidates.append(candidate)
    return ", ".join(candidates)


def rewrite_css(value: str, base: str) -> str:
    return CSS_URL.sub(
        lambda match: "url(" + match.group(1)
        + prefix_url(match.group(2), base) + match.group(1) + ")",
        value,
    )


def copy_source(source: Path, target: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = set(names) & SKIP_NAMES
        if Path(directory) == source:
            ignored |= {"README.md", ".nojekyll"}
        return ignored

    shutil.copytree(source, target, ignore=ignore)


def rewrite_file(path: Path, base: str) -> None:
    content = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".html", ".htm"}:
        content = HTML_URL_ATTRS.sub(
            lambda match: match.group("attr") + match.group("quote")
            + prefix_url(match.group("value"), base) + match.group("quote"),
            content,
        )
        content = SRCSET_ATTR.sub(
            lambda match: match.group(1) + match.group(2)
            + rewrite_srcset(match.group(3), base) + match.group(4),
            content,
        )
        content = STYLE_ATTR.sub(
            lambda match: match.group(1) + match.group(2)
            + rewrite_css(match.group(3), base) + match.group(4),
            content,
        )
        content = STYLE_BLOCK.sub(
            lambda match: match.group(1) + rewrite_css(match.group(2), base) + match.group(3),
            content,
        )
    elif path.suffix.lower() == ".css":
        content = rewrite_css(content, base)
    path.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="Pages project base path, such as /rocket-static-site")
    parser.add_argument("--output", default="_site")
    args = parser.parse_args()

    base = "/" + args.base.strip("/")
    source = Path.cwd().resolve()
    target = Path(args.output).resolve()
    if target == source or source not in target.parents:
        raise SystemExit("Output must be a child directory of the repository")
    if target.exists():
        shutil.rmtree(target)
    copy_source(source, target)

    for path in target.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".html", ".htm", ".css"}:
            rewrite_file(path, base)

    (target / ".nojekyll").touch()


if __name__ == "__main__":
    main()
