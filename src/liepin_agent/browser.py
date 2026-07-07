from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from liepin_agent.models import Candidate
from liepin_agent.settings import AppConfig, EnvSettings


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _safe_text(element) -> str:
    try:
        return _normalize_space(element.inner_text(timeout=1500))
    except Exception:
        return ""


class LiepinBrowser:
    def __init__(self, config: AppConfig, env: EnvSettings) -> None:
        self.config = config
        self.env = env
        self._playwright = None
        self._browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def __enter__(self) -> "LiepinBrowser":
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.config.browser.headless,
            slow_mo=self.config.browser.slow_mo_ms,
        )
        state_path = Path(self.config.browser.storage_state_path)
        context_kwargs = {}
        if state_path.exists():
            context_kwargs["storage_state"] = str(state_path)
        self.context = self._browser.new_context(**context_kwargs)
        self.page = self.context.new_page()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.context:
            self._save_state()
            self.context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def login(self) -> None:
        page = self._require_page()
        page.goto(self.config.search.url, wait_until="domcontentloaded")
        selectors = self.config.browser.selectors

        can_auto_login = all(
            [
                self.env.liepin_username,
                self.env.liepin_password,
                selectors.username_input,
                selectors.password_input,
                selectors.login_button,
            ]
        )
        if can_auto_login:
            page.fill(selectors.username_input, self.env.liepin_username)
            page.fill(selectors.password_input, self.env.liepin_password)
            page.click(selectors.login_button)
            page.wait_for_load_state("networkidle", timeout=15000)
        else:
            print("请在打开的浏览器窗口中完成猎聘登录/验证。完成后回到终端按 Enter 保存会话。")
            input()

        self._save_state()

    def search_candidates(self) -> list[Candidate]:
        page = self._require_page()
        page.goto(self.config.search.url, wait_until="domcontentloaded")
        self._fill_search_form(page)
        self._click_search(page)
        page.wait_for_load_state("networkidle", timeout=15000)
        return self._extract_candidates(page)

    def send_greeting(self, candidate: Candidate, message: str) -> bool:
        page = self._require_page()
        selectors = self.config.browser.selectors
        if not candidate.profile_url:
            return False
        page.goto(candidate.profile_url, wait_until="domcontentloaded")
        page.click(selectors.greet_button, timeout=5000)
        page.fill(selectors.greeting_textarea, message)
        if self.env.dry_run:
            return False
        page.click(selectors.send_button)
        page.wait_for_load_state("networkidle", timeout=10000)
        return True

    def _fill_search_form(self, page: Page) -> None:
        selectors = self.config.browser.selectors
        keywords = " ".join(self.config.search.keywords)
        if keywords:
            page.locator(selectors.keyword_input).first.fill(keywords)
        # City/experience/education filters vary by account and page version.
        # Keep them in config for search intent; selectors can be added once observed.

    def _click_search(self, page: Page) -> None:
        selectors = self.config.browser.selectors
        try:
            page.locator(selectors.search_button).filter(has_text=re.compile("搜索|查询")).first.click(timeout=5000)
        except Exception:
            page.locator(selectors.search_button).first.click(timeout=5000)

    def _extract_candidates(self, page: Page) -> list[Candidate]:
        selectors = self.config.browser.selectors
        cards = page.locator(selectors.candidate_cards)
        count = min(cards.count(), self.config.search.max_candidates)
        candidates: list[Candidate] = []
        for index in range(count):
            card = cards.nth(index)
            text = _safe_text(card)
            if not text:
                continue
            link = ""
            try:
                href = card.locator(selectors.candidate_link).first.get_attribute("href", timeout=1000)
                if href:
                    link = urljoin(page.url, href)
            except Exception:
                pass

            candidate = self._candidate_from_text(text, link, index)
            if link:
                candidate.resume_text = self._load_resume_text(link) or candidate.resume_text
            candidates.append(candidate)
        return candidates

    def _load_resume_text(self, url: str) -> str:
        if not self.context:
            return ""
        page = self.context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_load_state("networkidle", timeout=10000)
            locator = page.locator(self.config.browser.selectors.resume_text).first
            html = locator.inner_html(timeout=5000)
            soup = BeautifulSoup(html, "html.parser")
            return _normalize_space(soup.get_text(" "))
        except Exception:
            return ""
        finally:
            page.close()

    def _candidate_from_text(self, text: str, link: str, index: int) -> Candidate:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        name = lines[0] if lines else ""
        title = lines[1] if len(lines) > 1 else ""
        return Candidate(
            candidate_id=f"candidate-{index + 1}",
            name=name[:40],
            title=title[:80],
            profile_url=link,
            resume_text=text,
            raw={"card_text": text},
        )

    def _save_state(self) -> None:
        if not self.context:
            return
        state_path = Path(self.config.browser.storage_state_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        self.context.storage_state(path=str(state_path))

    def _require_page(self) -> Page:
        if not self.page:
            raise RuntimeError("Browser is not started.")
        return self.page

