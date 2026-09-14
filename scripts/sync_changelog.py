#!/usr/bin/env python3
"""Generate the public changelog tree from an ai-web-engine git checkout."""
from __future__ import annotations

import argparse
import html
import pathlib
import re
import subprocess
from dataclasses import dataclass

TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


@dataclass(frozen=True)
class Release:
    tag: str
    version: str
    major: int
    commit: str
    date: str
    subject: str
    body: str
    commits: tuple[str, ...]


def git(source: pathlib.Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()


def releases(source: pathlib.Path) -> list[Release]:
    tags = []
    for tag in git(source, "tag", "--list", "v*", "--sort=-v:refname").splitlines():
        match = TAG_RE.match(tag.strip())
        if match:
            tags.append((tag.strip(), tuple(map(int, match.groups()))))
    result = []
    for index, (tag, parts) in enumerate(tags):
        commit = git(source, "rev-list", "-n", "1", tag)
        raw = git(source, "show", "-s", "--format=%aI%n%s%n%b", tag).splitlines()
        date = raw[0][:10] if raw else "unknown"
        subject = raw[1] if len(raw) > 1 else ""
        body = "\n".join(raw[2:]).strip()
        older = tags[index + 1][0] if index + 1 < len(tags) else ""
        commit_range = f"{older}..{tag}" if older else tag
        subjects = tuple(line for line in git(source, "log", "--format=%s", commit_range).splitlines() if line)
        result.append(Release(tag, ".".join(map(str, parts)), parts[0], commit, date, subject, body, subjects))
    return result


def markdown(release: Release) -> str:
    lines = [f"# ai-web-engine {release.version}", "", f"- 发布标签：`{release.tag}`", f"- 提交：`{release.commit}`", f"- 日期：`{release.date}`", "", "## 版本主题", "", f"- {release.subject or '（该标签未提供主题）'}"]
    if release.body:
        lines += ["", "## 发布说明", "", release.body]
    lines += ["", "## 此版本提交记录", ""]
    lines += [f"- {subject}" for subject in release.commits] or ["- 该标签没有可单独列出的提交记录。"]
    lines += ["", "## 快速跳转", "", "- [返回全量更新日志](../../更新日志.md)", "- [返回 Pages 首页](../../docs/index.html)", ""]
    return "\n".join(lines)


def root_markdown(items: list[Release]) -> str:
    lines = ["# ai-web-engine 更新日志", "", "> 本文档由 `ai-web-engine` 的正式 Git tag 自动生成；最新版本在最前。", "", "## 版本导航", ""]
    for release in items:
        lines.append(f"- [{release.version}](更新日志/{release.major}/{release.version}.md) · `{release.date}` · [{release.subject or '查看详情'}](更新日志/{release.major}/{release.version}.md)")
    lines += ["", "## 历史版本", "", "每个版本的提交主题、标签提交、日期和版本内提交记录位于对应大版本目录。", ""]
    return "\n".join(lines)


def release_page(release: Release) -> str:
    content = markdown(release)
    body = html.escape(content)
    return f"<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>ai-web-engine {html.escape(release.version)}</title><style>body{{font:16px/1.65 system-ui,sans-serif;max-width:960px;margin:auto;padding:2rem}}a{{color:#0969da}}</style><body><p><a href=\"../../index.html\">返回更新日志首页</a></p><pre style=\"white-space:pre-wrap\">{body}</pre></body></html>"


def page(items: list[Release]) -> str:
    nav = "".join(f'<li><a href="更新日志/{r.major}/{r.version}.html">{html.escape(r.version)}</a> — {html.escape(r.subject)}</li>' for r in items)
    cards = "".join(f'<article id="v{html.escape(r.version)}"><h2><a href="更新日志/{r.major}/{r.version}.html">{html.escape(r.version)}</a></h2><p>{html.escape(r.date)} · <code>{html.escape(r.commit[:12])}</code></p><p>{html.escape(r.subject)}</p></article>' for r in items)
    return f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ai-web-engine 更新日志</title>
<style>body{{font:16px/1.65 system-ui,sans-serif;max-width:960px;margin:auto;padding:2rem;color:#18202a}}a{{color:#0969da}}article{{border:1px solid #d0d7de;border-radius:12px;padding:1rem;margin:1rem 0}}code{{background:#f6f8fa;padding:.15rem .3rem;border-radius:4px}}</style>
<body><h1>ai-web-engine 更新日志</h1><p>正式版本更新记录，最新版本在最前。</p><h2>快速跳转</h2><ul>{nav}</ul><main>{cards}</main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    items = releases(args.source)
    if not items:
        raise SystemExit("no semantic vX.Y.Z tags found")
    (args.out / "更新日志").mkdir(parents=True, exist_ok=True)
    (args.out / "docs").mkdir(parents=True, exist_ok=True)
    (args.out / "更新日志.md").write_text(root_markdown(items), encoding="utf-8")
    for release in items:
        target = args.out / "更新日志" / str(release.major) / f"{release.version}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown(release), encoding="utf-8")
        page_target = args.out / "docs" / "更新日志" / str(release.major) / f"{release.version}.html"
        page_target.parent.mkdir(parents=True, exist_ok=True)
        page_target.write_text(release_page(release), encoding="utf-8")
    (args.out / "docs" / "index.html").write_text(page(items), encoding="utf-8")


if __name__ == "__main__":
    main()
