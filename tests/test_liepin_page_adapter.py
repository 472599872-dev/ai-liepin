from liepin_agent.liepin_page_adapter import LiepinPageAdapter


class FakePage:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.values: dict[str, str] = {}

    def runJavaScript(self, script, callback=None):
        self.calls.append(script)
        value = ""
        if script.strip().startswith("window."):
            name = script.strip().removeprefix("window.").split()[0].strip(";")
            value = self.values.get(name, "")
        if callback:
            callback(value)


class FakeWeb:
    def __init__(self, page: FakePage) -> None:
        self._page = page

    def page(self):
        return self._page

    def url(self):
        class Url:
            def toString(self) -> str:
                return "https://h.liepin.com/search/getConditionItem"

        return Url()


def test_run_async_json_script_polls_and_clears_result() -> None:
    page = FakePage()
    timers = []
    adapter = LiepinPageAdapter(FakeWeb(page), lambda _delay, callback: timers.append(callback))
    seen = []

    adapter.run_async_json_script(
        result_var="__result__",
        body_script="Promise.resolve({ok:true})",
        callback=seen.append,
        poll_interval_ms=1,
        max_poll_attempts=3,
    )
    page.values["__result__"] = '{"ok":true}'
    timers.pop(0)()

    assert seen == ['{"ok":true}']
    assert any("window.__result__ = '';" in call for call in page.calls)


def test_run_async_json_script_times_out() -> None:
    page = FakePage()
    timers = []
    adapter = LiepinPageAdapter(FakeWeb(page), lambda _delay, callback: timers.append(callback))
    seen = []

    adapter.run_async_json_script(
        result_var="__missing__",
        body_script="Promise.resolve({ok:true})",
        callback=seen.append,
        poll_interval_ms=1,
        max_poll_attempts=2,
    )
    while timers:
        timers.pop(0)()

    assert seen == [""]
