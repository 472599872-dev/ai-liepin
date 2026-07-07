from __future__ import annotations

import json
from typing import Any, Callable

from liepin_agent.liepin_scripts import (
    COLLECT_CARDS_JS,
    CLICK_NEXT_PAGE_JS,
    GREET_JS,
    INSPECT_RESUME_JS,
    LOGIN_FILL_JS,
    LOGIN_STATUS_JS,
    LOGIN_SWITCH_PASSWORD_JS,
    OPEN_CANDIDATE_BY_INDEX_JS,
    PREPARE_RESUME_JS,
    RECORDER_START_JS,
    RECORDER_STOP_JS,
    ROUTE_APPLY_CONDITIONS_JS,
    ROUTE_CLICK_SEARCH_BUTTON_JS,
    SEARCH_JS,
    TOGGLE_RESULT_FILTER_JS,
)


JsonCallback = Callable[[Any], None]
TimerFactory = Callable[[int, Callable[[], None]], None]


class LiepinPageAdapter:
    """Thin Qt-WebEngine adapter around Liepin DOM scripts.

    This layer intentionally contains no recruiting business rules. It only
    centralizes how desktop code invokes page scripts, making future executor
    tests and page-operation replacement possible.
    """

    def __init__(self, web: Any, set_timer: TimerFactory) -> None:
        self.web = web
        self.set_timer = set_timer

    def page(self) -> Any:
        return self.web.page()

    def current_url(self) -> str:
        try:
            return self.web.url().toString()
        except Exception:
            return ""

    def run_js(self, script: str, callback: JsonCallback | None = None) -> None:
        handler = callback or (lambda _value: None)
        try:
            self.page().runJavaScript(script, handler)
        except Exception as exc:
            handler(
                {
                    "error": "runJavaScript 调用失败",
                    "detail": str(exc),
                    "url": self.current_url(),
                }
            )

    def run_async_json_script(
        self,
        *,
        result_var: str,
        body_script: str,
        callback: JsonCallback,
        poll_interval_ms: int = 200,
        max_poll_attempts: int = 450,
    ) -> None:
        start_script = f"""
        (() => {{
          const finish = payload => {{
            try {{
              window.{result_var} = typeof payload === 'string' ? payload : JSON.stringify(payload || {{}});
            }} catch (jsonError) {{
              window.{result_var} = JSON.stringify({{
                error: '脚本结果序列化失败',
                detail: String((jsonError && jsonError.message) || jsonError || 'unknown'),
              }});
            }}
          }};
          window.{result_var} = '';
          try {{
            const __runner = {body_script}
            Promise.resolve(__runner)
              .then(value => finish(value || {{}}))
              .catch(error => finish({{
                error: String((error && error.message) || error || 'unknown error'),
                stack: error && error.stack ? String(error.stack) : '',
              }}));
          }} catch (error) {{
            finish({{
              error: String((error && error.message) || error || 'unknown error'),
              stack: error && error.stack ? String(error.stack) : '',
            }});
          }}
          return 'started';
        }})();
        """
        state = {"attempts": 0}

        def poll_result() -> None:
            state["attempts"] += 1

            def handle_poll(value: Any) -> None:
                if value:
                    callback(value)
                    self.run_js(f"window.{result_var} = '';")
                    return
                if state["attempts"] >= max(1, int(max_poll_attempts)):
                    callback("")
                    self.run_js(f"window.{result_var} = '';")
                    return
                self.set_timer(max(0, int(poll_interval_ms)), poll_result)

            self.run_js(f"window.{result_var}", handle_poll)

        self.run_js(start_script, lambda _value: self.set_timer(max(0, int(poll_interval_ms)), poll_result))

    def switch_password_login(self, callback: JsonCallback) -> None:
        self.run_js(LOGIN_SWITCH_PASSWORD_JS, callback)

    def fill_login(self, username: str, password: str, submit: bool, callback: JsonCallback) -> None:
        self.run_js(LOGIN_FILL_JS % (username, password, "true" if submit else "false"), callback)

    def check_login_status(self, callback: JsonCallback) -> None:
        self.run_js(LOGIN_STATUS_JS, callback)

    def apply_conditions(self, payload: dict[str, Any], callback: JsonCallback, *, result_var: str, poll_interval_ms: int, max_poll_attempts: int) -> None:
        self.run_async_json_script(
            result_var=result_var,
            body_script=ROUTE_APPLY_CONDITIONS_JS % json.dumps(payload, ensure_ascii=False),
            callback=callback,
            poll_interval_ms=poll_interval_ms,
            max_poll_attempts=max_poll_attempts,
        )

    def click_search(self, hints: list[dict[str, str]], callback: JsonCallback) -> None:
        self.run_js(ROUTE_CLICK_SEARCH_BUTTON_JS % json.dumps(hints, ensure_ascii=False), callback)

    def collect_cards(self, callback: JsonCallback) -> None:
        self.run_js(COLLECT_CARDS_JS, callback)

    def click_next_page(self, callback: JsonCallback) -> None:
        self.run_js(CLICK_NEXT_PAGE_JS, callback)

    def toggle_result_filter(self, label: str, callback: JsonCallback) -> None:
        self.run_js(TOGGLE_RESULT_FILTER_JS % (label, label), callback)

    def open_candidate_by_index(self, index: int, callback: JsonCallback) -> None:
        self.run_js(OPEN_CANDIDATE_BY_INDEX_JS % max(0, int(index)), callback)

    def prepare_resume(self, callback: JsonCallback) -> None:
        self.run_js(PREPARE_RESUME_JS, callback)

    def inspect_resume(self, callback: JsonCallback) -> None:
        self.run_js(INSPECT_RESUME_JS, callback)

    def fill_search_keywords(self, keywords: str, callback: JsonCallback) -> None:
        self.run_js(SEARCH_JS % keywords, callback)

    def start_recording(self, callback: JsonCallback) -> None:
        self.run_js(RECORDER_START_JS, callback)

    def stop_recording(self, callback: JsonCallback) -> None:
        self.run_js(RECORDER_STOP_JS, callback)

    def greet(
        self,
        opening_greeting: str,
        followup: str,
        continued_followup: str,
        dry_run: bool,
        callback: JsonCallback,
        *,
        result_var: str,
        poll_interval_ms: int = 200,
        max_poll_attempts: int = 450,
    ) -> None:
        self.run_async_json_script(
            result_var=result_var,
            body_script=GREET_JS % (opening_greeting, followup, continued_followup, "true" if dry_run else "false"),
            callback=callback,
            poll_interval_ms=poll_interval_ms,
            max_poll_attempts=max_poll_attempts,
        )
