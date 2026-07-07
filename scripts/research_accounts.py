from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


SEARCH_URL = "https://h.liepin.com/search/getConditionItem"


def visible_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""


def inspect_search_page(page) -> dict[str, Any]:
    text = visible_text(page)
    inputs = page.locator("input, textarea").evaluate_all(
        """
        els => els.map((e, i) => ({
          i,
          tag: e.tagName,
          type: e.type || '',
          placeholder: e.placeholder || '',
          value: e.value || '',
          cls: String(e.className || '').slice(0, 120)
        })).slice(0, 80)
        """
    )
    buttons = page.locator("button, a, [role=button]").evaluate_all(
        """
        els => els.map((e, i) => ({
          i,
          text: (e.innerText || e.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 100),
          cls: String(e.className || '').slice(0, 120),
          href: e.href || ''
        })).filter(x => x.text).slice(0, 120)
        """
    )
    cards = page.locator(".detail-resume-card-wrap, .tlog-common-resume-card, tr").evaluate_all(
        """
        els => els.map((e, i) => {
          const text = (e.innerText || e.textContent || '').trim().replace(/\\s+/g, ' ');
          return { i, text: text.slice(0, 260), textLength: text.length };
        }).filter(x => /求职期望|立即沟通|工作\\d+年|本科|硕士|博士|大专/.test(x.text)).slice(0, 30)
        """
    )
    return {
        "url": page.url,
        "title": page.title(),
        "text_length": len(text),
        "has_search_filters": "工作年限" in text and "教育经历" in text,
        "has_candidate_cards": bool(cards),
        "filter_signals": [word for word in ["目前城市", "期望城市", "工作年限", "教育经历", "当前行业", "当前职位", "年龄", "活跃度", "隐藏已沟通"] if word in text],
        "button_signals": [item["text"] for item in buttons if item["text"] in {"搜 索", "批量查看", "立即沟通", "保存条件"}][:20],
        "inputs": inputs,
        "buttons": buttons,
        "cards": cards,
    }


def login_and_research(account: dict[str, str], headed: bool) -> dict[str, Any]:
    profile = Path("profiles") / f"research_{account['name']}"
    profile.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"name": account["name"], "username": account["username"], "profile": str(profile)}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(profile.resolve()),
            channel="chrome",
            headless=not headed,
            viewport={"width": 1440, "height": 950},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        result["initial_url"] = page.url
        result["initial_title"] = page.title()

        if "/account/login" in page.url:
            try:
                page.get_by_text("密码登录").click(timeout=5000)
                page.wait_for_timeout(500)
                page.locator("input[placeholder='请输入认证的手机号/邮箱']").fill(account["username"])
                page.locator("input[placeholder='请输入登录密码']").fill(account["password"])
                page.get_by_role("button", name="登 录").click()
                page.wait_for_timeout(5000)
            except PlaywrightTimeoutError as exc:
                result["login_error"] = f"login form timeout: {exc}"
            except Exception as exc:
                result["login_error"] = f"login form error: {exc}"

        result["after_login_url"] = page.url
        result["after_login_title"] = page.title()
        text = visible_text(page)
        result["login_state"] = classify_state(page.url, text)
        result["page_text_preview"] = text.replace("\n", " ")[:800]

        if result["login_state"] == "logged_in_or_search":
            try:
                result["search_page"] = inspect_search_page(page)
            except Exception as exc:
                result["search_inspect_error"] = str(exc)

        ctx.close()
    return result


def classify_state(url: str, text: str) -> str:
    if "验证码" in text or "安全验证" in text or "拖动" in text:
        return "needs_verification"
    if "/account/login" in url:
        return "needs_login_or_failed"
    if "找简历" in text and "工作年限" in text:
        return "logged_in_or_search"
    return "unknown"


def main() -> None:
    payload = json.loads(sys.stdin.read())
    headed = bool(payload.get("headed", False))
    accounts = payload["accounts"]
    output_path = Path("data/account_research.jsonl")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    for account in accounts:
        result = login_and_research(account, headed=headed)
        safe = {k: v for k, v in result.items() if k != "password"}
        with output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe, ensure_ascii=False) + "\n")
        print(json.dumps({k: safe.get(k) for k in ["name", "username", "login_state", "after_login_url"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

