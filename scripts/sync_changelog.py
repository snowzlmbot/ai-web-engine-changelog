#!/usr/bin/env python3
"""Generate the public changelog tree and Pages site from formal releases."""
from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
DEFAULT_REPO = "snowzlmbot/ai-web-engine"


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Release:
    tag: str
    version: str
    major: int
    commit: str
    date: str
    subject: str
    body: str
    notes: str
    commits: tuple[str, ...]
    release_url: str
    published_at: str
    updated_at: str
    assets: tuple[Asset, ...]


def git(source: pathlib.Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(source), *args], text=True, stderr=subprocess.DEVNULL).strip()


def api_json(url: str) -> dict | list | None:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "ai-web-engine-changelog"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def checksum_map(url: str) -> dict[str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "ai-web-engine-changelog"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            text = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError):
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            values[pathlib.PurePosixPath(parts[-1]).name] = parts[0].lower()
    return values


def release_metadata(repo: str, tag: str) -> tuple[str, str, str, tuple[Asset, ...]]:
    payload = api_json(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
    if not isinstance(payload, dict):
        fallback = f"https://github.com/{repo}/releases/tag/{tag}"
        return fallback, "", "", ()
    release_url = str(payload.get("html_url") or f"https://github.com/{repo}/releases/tag/{tag}")
    published_at = str(payload.get("published_at") or payload.get("created_at") or "")
    updated_at = str(payload.get("updated_at") or published_at)
    raw_assets_value = payload.get("assets")
    raw_assets = [asset for asset in raw_assets_value if isinstance(asset, dict)] if isinstance(raw_assets_value, list) else []
    checksum_asset = next((a for a in raw_assets if a.get("name") == "SHA256SUMS"), None)
    checksums = checksum_map(str(checksum_asset.get("browser_download_url"))) if checksum_asset else {}
    assets = tuple(
        Asset(
            name=str(asset.get("name") or ""),
            url=str(asset.get("browser_download_url") or ""),
            size=int(asset.get("size") or 0),
            sha256=checksums.get(pathlib.PurePosixPath(str(asset.get("name") or "")).name, ""),
        )
        for asset in raw_assets
        if asset.get("name") and asset.get("browser_download_url")
    )
    return release_url, published_at, updated_at, tuple(assets)


def changelog_notes(source: pathlib.Path, tag: str, version: str) -> str:
    try:
        text = git(source, "show", f"{tag}:CHANGELOG.md")
    except subprocess.CalledProcessError:
        return ""
    match = re.search(rf"^## \[?{re.escape(version)}\]?[^\n]*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def releases(source: pathlib.Path, repo: str) -> list[Release]:
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
        version = ".".join(map(str, parts))
        release_url, published_at, updated_at, assets = release_metadata(repo, tag)
        notes = changelog_notes(source, tag, version)
        result.append(Release(tag, version, parts[0], commit, date, subject, body, notes, subjects, release_url, published_at, updated_at, assets))
    return result


def asset_lines(release: Release) -> list[str]:
    if not release.assets:
        return ["- 暂无可读回的 GitHub Release 资产。"]
    lines = []
    for asset in release.assets:
        label = f"[{asset.name}]({asset.url})"
        suffix = f" · {asset.size} bytes" if asset.size else ""
        if asset.sha256:
            suffix += f" · SHA-256 `{asset.sha256}`"
        lines.append(f"- {label}{suffix}")
    return lines


def markdown(release: Release) -> str:
    lines = [
        f"# ai-web-engine {release.version}",
        "",
        f"- 发布标签：[`{release.tag}`]({release.release_url})",
        f"- 提交：[`{release.commit}`](https://github.com/snowzlmbot/ai-web-engine/commit/{release.commit})",
        f"- 日期：`{release.date}`",
        f"- Release 最后编辑/发布时间：`{release.updated_at or release.published_at or '未提供'}`",
        "",
        "## 版本主题",
        "",
        f"- {release.subject or '（该标签未提供主题）'}",
        "",
        "## 发行资产与 SHA-256",
        "",
        *asset_lines(release),
    ]
    if release.notes:
        lines += ["", "## 变更详情", "", release.notes]
    if release.body:
        lines += ["", "## 发布说明", "", release.body]
    lines += ["", "## 此版本提交记录", ""]
    lines += [f"- {subject}" for subject in release.commits] or ["- 该标签没有可单独列出的提交记录。"]
    lines += ["", "## 快速跳转", "", "- [返回全量更新日志](../../更新日志.md)", "- [返回 Pages 首页](../../docs/index.html)", ""]
    return "\n".join(lines)


def root_markdown(items: list[Release]) -> str:
    lines = ["# ai-web-engine 更新日志", "", "> 本文档由正式 Git tag 和 GitHub Release 自动生成；最新版本在最前。", "", "## 版本导航", ""]
    for release in items:
        lines.append(f"- [{release.version}](更新日志/{release.major}/{release.version}.md) · `{release.date}` · [{release.subject or '查看详情'}](更新日志/{release.major}/{release.version}.md) · [GitHub Release]({release.release_url})")
    lines += ["", "## 历史版本", "", "每个版本的提交主题、真实 Release 资产、SHA-256 和版本内提交记录位于对应大版本目录。", ""]
    return "\n".join(lines)


def release_page(release: Release) -> str:
    content = html.escape(markdown(release))
    edited = release.updated_at or release.published_at or release.date
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ai-web-engine {html.escape(release.version)}</title><style>:root{{--bg:#f6f8fa;--card:#fff;--ink:#17202a;--muted:#667085;--accent:#0969da}}body{{font:16px/1.7 system-ui,sans-serif;background:var(--bg);color:var(--ink);max-width:980px;margin:auto;padding:2rem}}main{{background:var(--card);border-radius:20px;padding:2rem;box-shadow:0 10px 30px #17202a12}}a{{color:var(--accent)}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}.meta{{color:var(--muted)}}.reader{{float:right}}</style><body><main><p><a href="../../index.html">← 返回更新日志首页</a></p><p class="meta">最后编辑：{html.escape(edited)} <span class="reader">阅读 <b id="reader-count">统计中…</b></span></p><pre>{content}</pre></main><script>fetch('https://api.counterapi.dev/v1/ai-web-engine-changelog/v-{html.escape(release.version.replace('.', '-'))}/up').then(r=>r.ok?r.json():Promise.reject()).then(d=>document.querySelector('#reader-count').textContent=String(d.count??d.value??'—')).catch(()=>document.querySelector('#reader-count').textContent='—');</script></body></html>'''


def page(items: list[Release]) -> str:
    nav = "".join(f'<li><a href="#v-{html.escape(r.version.replace(".", "-"))}">{html.escape(r.version)}</a> · <a href="更新日志/{r.major}/{r.version}.html">详情</a></li>' for r in items)
    cards = "".join(
        f'<article id="v-{html.escape(r.version.replace(".", "-"))}"><div class="tag">{html.escape(r.tag)}</div><h2><a href="更新日志/{r.major}/{r.version}.html">{html.escape(r.version)}</a></h2><p class="meta">发布：{html.escape(r.date)} · 最后编辑/发布时间：{html.escape(r.published_at or "未提供")} · <code>{html.escape(r.commit[:12])}</code></p><p>{html.escape(r.subject)}</p><p><a class="button" href="{html.escape(r.release_url)}" target="_blank" rel="noreferrer">打开 GitHub Release</a> <a href="更新日志/{r.major}/{r.version}.html">查看完整记录 →</a></p></article>'
        for r in items
    )
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ai-web-engine 更新日志</title><style>:root{{--bg:#0b1220;--panel:#121c2e;--ink:#edf4ff;--muted:#9fb0c8;--accent:#72b7ff;--line:#263754}}*{{box-sizing:border-box}}body{{margin:0;font:16px/1.65 system-ui,-apple-system,sans-serif;background:radial-gradient(circle at top right,#19355b 0,#0b1220 46%);color:var(--ink)}}.wrap{{max-width:1100px;margin:auto;padding:clamp(1.2rem,4vw,3rem)}}header{{display:flex;justify-content:space-between;gap:1rem;align-items:end;margin-bottom:2rem}}h1{{font-size:clamp(2rem,5vw,3.5rem);line-height:1.1;margin:.3rem 0}}h2{{margin:.35rem 0;font-size:1.6rem}}.sub,.meta{{color:var(--muted)}}.stats{{display:flex;gap:.7rem;flex-wrap:wrap}}.stat,.tag,.button{{border:1px solid var(--line);border-radius:999px;padding:.35rem .7rem;background:#ffffff0b;color:var(--muted)}}.quick{{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:1rem 1.2rem;margin-bottom:1.2rem}}.quick ul{{display:flex;flex-wrap:wrap;gap:.7rem 1.2rem;margin:.5rem 0 0;padding-left:1.2rem}}a{{color:var(--accent);text-decoration:none}}a:hover{{text-decoration:underline}}main{{display:grid;gap:1rem}}article{{background:linear-gradient(145deg,#16243b,#101a2b);border:1px solid var(--line);border-radius:20px;padding:1.3rem 1.4rem;scroll-margin-top:1rem}}article:hover{{border-color:#4e83bd;transform:translateY(-1px);transition:.2s}}.tag{{display:inline-block;font-size:.85rem}}.button{{display:inline-block;color:var(--ink);margin-right:.5rem}}footer{{margin-top:2rem;color:var(--muted);font-size:.9rem}}#reader-count{{font-variant-numeric:tabular-nums}}</style><body><div class="wrap"><header><div><div class="tag">PUBLIC RELEASE ARCHIVE</div><h1>ai-web-engine<br>更新日志</h1><p class="sub">正式版本、变更详情、发行资产与 SHA-256 校验信息。</p></div><div class="stats"><span class="stat">版本 {len(items)}</span><span class="stat">阅读 <b id="reader-count">统计中…</b></span></div></header><section class="quick"><b>快速跳转</b><ul>{nav}</ul></section><main>{cards}</main><footer>阅读统计为匿名页面计数，不读取硬件标识或用户身份。最后生成版本：{html.escape(items[0].published_at or items[0].date if items else '未知')} · <a href="https://github.com/snowzlmbot/ai-web-engine-changelog" target="_blank" rel="noreferrer">查看仓库</a></footer></div><script>const key='ai-web-engine-changelog-home';fetch('https://api.counterapi.dev/v1/ai-web-engine-changelog/home/up').then(r=>r.ok?r.json():Promise.reject()).then(d=>{{document.querySelector('#reader-count').textContent=String(d.count??d.value??'—')}}).catch(()=>{{document.querySelector('#reader-count').textContent='—'}});</script></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    args = parser.parse_args()
    items = releases(args.source, args.repo)
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
