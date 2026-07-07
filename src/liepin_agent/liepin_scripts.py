SEARCH_PAGE_URL = "https://h.liepin.com/search/getConditionItem"


LOGIN_SWITCH_PASSWORD_JS = """
(() => {
  const documents = [];
  const collectDocuments = doc => {
    if (!doc || documents.includes(doc)) return;
    documents.push(doc);
    for (const frame of [...doc.querySelectorAll('iframe')]) {
      try {
        if (frame.contentDocument) collectDocuments(frame.contentDocument);
      } catch (_) {}
    }
  };
  collectDocuments(document);
  const visible = el => {
    if (!el || !el.getBoundingClientRect) return false;
    try {
      const rect = el.getBoundingClientRect();
      const view = (el.ownerDocument && el.ownerDocument.defaultView) || window;
      const style = view.getComputedStyle ? view.getComputedStyle(el) : window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
    } catch (_) {
      return false;
    }
  };
  const clickLikeUser = el => {
    if (!el) return;
    el.scrollIntoView({ block: 'center', inline: 'center' });
    for (const eventName of ['pointerdown', 'mouseover', 'mousedown', 'mouseup', 'click']) {
      el.dispatchEvent(new MouseEvent(eventName, { bubbles: true, cancelable: true, view: window }));
    }
  };
  const tabs = documents.flatMap(doc => [...doc.querySelectorAll('li, [role="tab"], .login-tab li, .login-tab *')]);
  const passwordTab = tabs
    .filter(el => visible(el))
    .find(el => (el.innerText || el.textContent || '').replace(/\\s+/g, '').trim() === '密码登录');
  const beforeActive = tabs
    .filter(el => visible(el))
    .find(el => /active/i.test(String(el.className || '')));
  if (passwordTab && !/active/i.test(String(passwordTab.className || ''))) clickLikeUser(passwordTab);
  const inputs = documents.flatMap(doc => [...doc.querySelectorAll('input')]).filter(visible);
  return JSON.stringify({
    url: location.href,
    title: document.title,
    documentCount: documents.length,
    foundPasswordTab: Boolean(passwordTab),
    clickedPasswordTab: Boolean(passwordTab),
    beforeActiveText: beforeActive ? (beforeActive.innerText || beforeActive.textContent || '').trim() : '',
    inputCount: inputs.length,
    inputPlaceholders: inputs.map(el => el.placeholder || el.type || '').join(' | ')
  });
})();
"""


LOGIN_FILL_JS = """
(() => {
  const username = %r;
  const password = %r;
  const submitLogin = %s;
  const documents = [];
  const collectDocuments = doc => {
    if (!doc || documents.includes(doc)) return;
    documents.push(doc);
    for (const frame of [...doc.querySelectorAll('iframe')]) {
      try {
        if (frame.contentDocument) collectDocuments(frame.contentDocument);
      } catch (_) {}
    }
  };
  collectDocuments(document);
  const visible = el => {
    if (!el || !el.getBoundingClientRect) return false;
    try {
      const rect = el.getBoundingClientRect();
      const view = (el.ownerDocument && el.ownerDocument.defaultView) || window;
      const style = view.getComputedStyle ? view.getComputedStyle(el) : window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
    } catch (_) {
      return false;
    }
  };
  const fire = el => {
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
  };
  const setNativeValue = (el, value) => {
    const proto = el instanceof HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (setter) setter.call(el, value);
    else el.value = value;
    fire(el);
  };
  const clickLikeUser = el => {
    if (!el) return;
    el.scrollIntoView({ block: 'center', inline: 'center' });
    for (const eventName of ['pointerdown', 'mouseover', 'mousedown', 'mouseup', 'click']) {
      el.dispatchEvent(new MouseEvent(eventName, { bubbles: true, cancelable: true, view: window }));
    }
  };
  const inputs = documents.flatMap(doc => [...doc.querySelectorAll('input')]).filter(visible);
  const passwordInput = inputs.find(el => el.type === 'password' || /密码/.test(el.placeholder || ''));
  const container = passwordInput
    ? (passwordInput.closest('form, [class*="login"], [class*="Login"], [class*="content"], [class*="panel"]') || passwordInput.parentElement)
    : null;
  const scopedInputs = container ? [...container.querySelectorAll('input')].filter(visible) : inputs;
  const userInput = passwordInput
    ? (
        scopedInputs.find(el => el !== passwordInput && /手机|手机号|邮箱|账号|登录名/.test(el.placeholder || ''))
        || scopedInputs.find(el => el !== passwordInput && el.type !== 'hidden')
      )
    : null;
    if (userInput) {
      userInput.focus();
      setNativeValue(userInput, username);
    }
    if (passwordInput) {
      passwordInput.focus();
      setNativeValue(passwordInput, password);
    }
    const buttons = (container ? [...container.querySelectorAll('button, a, [role="button"]')] : documents.flatMap(doc => [...doc.querySelectorAll('button, a, [role="button"]')])).filter(visible);
    const loginButton = buttons.find(el => /^登\\s*录$|登录/.test((el.innerText || el.textContent || '').trim()));
    const bodyText = documents.map(doc => doc.body ? doc.body.innerText || '' : '').join('\\n');
    if (submitLogin && userInput && passwordInput && loginButton) {
      clickLikeUser(loginButton);
    }
    return JSON.stringify({
      url: location.href,
      title: document.title,
      documentCount: documents.length,
      inputCount: inputs.length,
      switchedPasswordLogin: Boolean(passwordInput),
      filledUsername: Boolean(userInput),
      filledPassword: Boolean(passwordInput),
      foundLoginButton: Boolean(loginButton),
      clickedLogin: Boolean(submitLogin && userInput && passwordInput && loginButton),
      passwordModeReady: Boolean(passwordInput),
      reason: passwordInput ? '' : '未检测到密码输入框，已停止填充，避免误填快捷登录。',
      loginModeText: bodyText.includes('密码登录') ? 'password_tab_visible' : (bodyText.includes('快捷登录') ? 'quick_login_visible' : ''),
      message: '已填账号密码，请人工点击登录并完成滑块/短信验证。'
    });
})();
"""


LOGIN_STATUS_JS = """
(() => {
  const documents = [];
  const collectDocuments = doc => {
    if (!doc || documents.includes(doc)) return;
    documents.push(doc);
    for (const frame of [...doc.querySelectorAll('iframe')]) {
      try {
        if (frame.contentDocument) collectDocuments(frame.contentDocument);
      } catch (_) {}
    }
  };
  collectDocuments(document);
  const text = documents.map(doc => doc.body ? doc.body.innerText || '' : '').join('\\n');
  const url = location.href;
  const isLoginUrl = /\\/account\\/login|passport|login/i.test(url);
  const hasLoginForm = /快捷登录|密码登录|请输入手机号码|请输入验证码|请输入密码|获取验证码/.test(text);
  const hasSearchPage = /\\/search\\/getConditionItem/.test(url) || /找人|找简历|搜职位\\/公司\\/行业|共\\s*\\d+\\s*位人选/.test(text);
  const needsVerification = /滑块|验证码|短信|获取验证码|请输入验证码|安全验证|拖动/.test(text);
  let status = 'unknown';
  if (hasSearchPage && !isLoginUrl && !hasLoginForm) status = 'logged_in';
  else if (isLoginUrl || hasLoginForm) status = needsVerification ? 'needs_verification' : 'needs_login';
  return JSON.stringify({
    url,
    title: document.title,
    status,
    isLoginUrl,
    hasLoginForm,
    hasSearchPage,
    needsVerification,
    documentCount: documents.length,
    textPreview: text.replace(/\\s+/g, ' ').trim().slice(0, 300)
  });
})();
"""


RECORDER_START_JS = """
(() => {
  const normalize = text => String(text || '').replace(/\\s+/g, ' ').trim();
  const limit = (text, n = 160) => {
    const value = normalize(text);
    return value.length <= n ? value : value.slice(0, n);
  };
  const safe = value => {
    try {
      return JSON.parse(JSON.stringify(value));
    } catch (_) {
      return String(value || '');
    }
  };
  const collectDocuments = () => {
    const docs = [];
    const seenWindows = new Set();
    const walk = win => {
      if (!win || seenWindows.has(win)) return;
      seenWindows.add(win);
      try {
        if (win.document) docs.push(win.document);
      } catch (_) {}
      let frames = [];
      try {
        frames = Array.from(win.frames || []);
      } catch (_) {}
      for (const frame of frames) {
        try {
          if (frame && frame !== win) walk(frame);
        } catch (_) {}
      }
      let frameEls = [];
      try {
        frameEls = win.document ? Array.from(win.document.querySelectorAll('iframe, frame')) : [];
      } catch (_) {}
      for (const frameEl of frameEls) {
        try {
          if (frameEl && frameEl.contentWindow) walk(frameEl.contentWindow);
        } catch (_) {}
      }
    };
    walk(window);
    return docs;
  };
  const visible = el => {
    if (!el || !el.ownerDocument) return false;
    const view = el.ownerDocument.defaultView || window;
    const rect = el.getBoundingClientRect();
    const style = view.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const describeElement = el => {
    if (!el) return {};
    const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : { left: 0, top: 0, width: 0, height: 0 };
    let docUrl = '';
    let docTitle = '';
    try {
      const view = el.ownerDocument ? el.ownerDocument.defaultView : null;
      docUrl = view && view.location ? String(view.location.href || '') : '';
      docTitle = el.ownerDocument ? String(el.ownerDocument.title || '') : '';
    } catch (_) {}
    return {
      tag: String(el.tagName || '').toLowerCase(),
      id: el.id || '',
      cls: String(el.className || '').slice(0, 120),
      role: el.getAttribute ? (el.getAttribute('role') || '') : '',
      name: el.getAttribute ? (el.getAttribute('name') || '') : '',
      placeholder: el.getAttribute ? (el.getAttribute('placeholder') || '') : '',
      text: limit(el.innerText || el.textContent || ''),
      docUrl,
      docTitle,
      rect: {
        left: Math.round(rect.left || 0),
        top: Math.round(rect.top || 0),
        width: Math.round(rect.width || 0),
        height: Math.round(rect.height || 0),
      },
    };
  };
  const inputValueForRecord = el => {
    if (!el || !('value' in el)) return '';
    const raw = String(el.value || '');
    const meta = [
      String(el.getAttribute ? (el.getAttribute('type') || '') : ''),
      String(el.getAttribute ? (el.getAttribute('name') || '') : ''),
      String(el.getAttribute ? (el.getAttribute('id') || '') : ''),
      String(el.getAttribute ? (el.getAttribute('placeholder') || '') : ''),
    ].join(' ');
    if (/password|pass|密码/i.test(meta)) {
      return raw ? `***(${raw.length})` : '';
    }
    return limit(raw);
  };
  const cityModalState = () => {
    const docs = collectDocuments();
    const all = [];
    for (const doc of docs) {
      try {
        const nodes = Array.from(doc.querySelectorAll('*')).filter(visible);
        all.push(...nodes);
      } catch (_) {}
    }
    const cityTitle = all.find(el => limit(el.innerText || el.textContent || '', 40) === '请选择城市');
    if (!cityTitle) return { open: false };
    const selectedNode = all.find(el => /已选[（(]\\s*\\d+\\s*\\/\\s*\\d+\\s*[）)]/.test(normalize(el.innerText || el.textContent || '')));
    const selectedText = selectedNode ? normalize(selectedNode.innerText || selectedNode.textContent || '') : '';
    const confirmButton = all.find(el => {
      if (!/button/i.test(String(el.tagName || ''))) return false;
      return /^(确认|确定)$/.test(normalize(el.innerText || el.textContent || ''));
    });
    const confirmEnabled = Boolean(confirmButton && !confirmButton.disabled && !String(confirmButton.className || '').includes('disabled'));
    return {
      open: true,
      selectedText,
      confirmEnabled,
      confirmText: confirmButton ? normalize(confirmButton.innerText || confirmButton.textContent || '') : '',
    };
  };
  const target = window.__liepinRecorder;
  if (target && target.active) {
    return JSON.stringify({
      ok: true,
      started: false,
      alreadyRunning: true,
      startAt: target.startAt,
      eventCount: Array.isArray(target.events) ? target.events.length : 0,
      url: location.href,
    });
  }
  const recorder = {
    active: true,
    startAt: Date.now(),
    events: [],
    maxEvents: 1400,
    boundDocs: [],
    boundSet: new Set(),
    observers: [],
    bindInterval: null,
  };
  const pushEvent = (type, payload) => {
    if (!recorder.active) return;
    const item = {
      t: Date.now() - recorder.startAt,
      type,
      url: location.href,
      title: document.title,
      cityModal: cityModalState(),
      payload: safe(payload),
    };
    recorder.events.push(item);
    if (recorder.events.length > recorder.maxEvents) recorder.events.shift();
  };
  recorder.refreshBindings = () => {
    const docs = collectDocuments();
    for (const doc of docs) {
      if (!doc || recorder.boundSet.has(doc)) continue;
      recorder.boundSet.add(doc);
      recorder.boundDocs.push(doc);
      try {
        doc.addEventListener('click', recorder.onClick, true);
        doc.addEventListener('input', recorder.onInput, true);
        doc.addEventListener('change', recorder.onChange, true);
        doc.addEventListener('keydown', recorder.onKeydown, true);
      } catch (_) {}
      try {
        const observer = new MutationObserver(mutations => {
          const summary = mutations.slice(0, 12).map(mutation => ({
            type: mutation.type,
            target: describeElement(mutation.target),
            added: mutation.addedNodes ? mutation.addedNodes.length : 0,
            removed: mutation.removedNodes ? mutation.removedNodes.length : 0,
            attr: mutation.attributeName || '',
          }));
          pushEvent('mutation', {
            count: mutations.length,
            summary,
            docTitle: String(doc.title || ''),
            docUrl: (() => {
              try {
                return doc.defaultView && doc.defaultView.location ? String(doc.defaultView.location.href || '') : '';
              } catch (_) {
                return '';
              }
            })(),
          });
        });
        observer.observe(doc.documentElement || doc.body, {
          subtree: true,
          childList: true,
          attributes: true,
          characterData: false,
          attributeFilter: ['class', 'style', 'aria-expanded', 'aria-selected', 'aria-checked', 'value'],
        });
        recorder.observers.push(observer);
      } catch (_) {}
      pushEvent('doc_bound', {
        docTitle: String(doc.title || ''),
        docUrl: (() => {
          try {
            return doc.defaultView && doc.defaultView.location ? String(doc.defaultView.location.href || '') : '';
          } catch (_) {
            return '';
          }
        })(),
      });
    }
  };
  recorder.onClick = event => {
    const targetEl = event.target;
    pushEvent('click', { target: describeElement(targetEl) });
  };
  recorder.onInput = event => {
    const targetEl = event.target;
    pushEvent('input', {
      target: describeElement(targetEl),
      value: inputValueForRecord(targetEl),
    });
  };
  recorder.onChange = event => {
    const targetEl = event.target;
    pushEvent('change', {
      target: describeElement(targetEl),
      value: inputValueForRecord(targetEl),
    });
  };
  recorder.onKeydown = event => {
    pushEvent('keydown', {
      key: event.key || '',
      code: event.code || '',
      target: describeElement(event.target),
    });
  };
  recorder.onHash = () => pushEvent('hashchange', { url: location.href });
  recorder.onPop = () => pushEvent('popstate', { url: location.href });
  window.addEventListener('hashchange', recorder.onHash, true);
  window.addEventListener('popstate', recorder.onPop, true);
  recorder.refreshBindings();
  recorder.bindInterval = window.setInterval(recorder.refreshBindings, 700);
  recorder.pushEvent = pushEvent;
  pushEvent('recorder_started', {
    url: location.href,
    title: document.title,
    docCount: recorder.boundDocs.length,
    cityModal: cityModalState(),
  });
  window.__liepinRecorder = recorder;
  return JSON.stringify({
    ok: true,
    started: true,
    url: location.href,
    title: document.title,
    docCount: recorder.boundDocs.length,
    cityModal: cityModalState(),
  });
})();
"""


RECORDER_STOP_JS = """
(() => {
  const recorder = window.__liepinRecorder;
  if (!recorder || !recorder.active) {
    return JSON.stringify({ ok: false, stopped: false, reason: 'recorder_not_running', url: location.href });
  }
  try {
    const boundDocs = Array.isArray(recorder.boundDocs) ? recorder.boundDocs : [];
    for (const doc of boundDocs) {
      try {
        doc.removeEventListener('click', recorder.onClick, true);
        doc.removeEventListener('input', recorder.onInput, true);
        doc.removeEventListener('change', recorder.onChange, true);
        doc.removeEventListener('keydown', recorder.onKeydown, true);
      } catch (_) {}
    }
    window.removeEventListener('hashchange', recorder.onHash, true);
    window.removeEventListener('popstate', recorder.onPop, true);
    const observers = Array.isArray(recorder.observers) ? recorder.observers : [];
    for (const observer of observers) {
      try {
        observer.disconnect();
      } catch (_) {}
    }
    if (recorder.bindInterval) window.clearInterval(recorder.bindInterval);
  } catch (_) {}
  recorder.active = false;
  const output = {
    ok: true,
    stopped: true,
    startAt: recorder.startAt,
    stopAt: Date.now(),
    durationMs: Date.now() - recorder.startAt,
    eventCount: Array.isArray(recorder.events) ? recorder.events.length : 0,
    events: Array.isArray(recorder.events) ? recorder.events : [],
    docCount: Array.isArray(recorder.boundDocs) ? recorder.boundDocs.length : 0,
    url: location.href,
    title: document.title,
  };
  window.__liepinRecorderLast = output;
  return JSON.stringify(output);
})();
"""


SEARCH_JS = """
(() => {
  const keywords = %r;
  const inputs = [...document.querySelectorAll('input, textarea')]
    .filter(el => !el.disabled && el.offsetParent !== null);
  const target = inputs.find(el => /keyword|key|search|query|请输入|职位|公司|候选人/i.test(
    [el.name, el.id, el.placeholder, el.getAttribute('aria-label')].filter(Boolean).join(' ')
  )) || inputs[0];
  if (target) {
    target.focus();
    target.value = keywords;
    target.dispatchEvent(new Event('input', { bubbles: true }));
    target.dispatchEvent(new Event('change', { bubbles: true }));
  }
  const buttons = [...document.querySelectorAll('button, a, [role="button"]')];
  const searchButton = buttons.find(el => /搜索|查询/.test(el.innerText || el.textContent || ''));
  if (searchButton) searchButton.click();
  return { filled: Boolean(target), clicked: Boolean(searchButton), keywords };
})();
"""


ROUTE_SEARCH_JS = """
(() => new Promise(resolve => {
  const keywords = %r;
  const conditionWords = %s;
  const documents = [];
  const collectDocuments = doc => {
    if (!doc || documents.includes(doc)) return;
    documents.push(doc);
    for (const frame of [...doc.querySelectorAll('iframe')]) {
      try {
        if (frame.contentDocument) collectDocuments(frame.contentDocument);
      } catch (_) {}
    }
  };
  collectDocuments(document);
  const visible = el => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const bodyText = documents.map(doc => doc.body ? doc.body.innerText || '' : '').join('\\n');
  if (/快捷登录|密码登录|请输入认证的手机号|请输入验证码/.test(bodyText)) {
    resolve({ ok: false, reason: '当前仍在登录页，请先完成人工登录/验证码。', url: location.href });
    return;
  }

  const inputInfo = el => {
    if (!el) return '';
    return [el.name, el.id, el.placeholder, el.getAttribute('aria-label'), el.getAttribute('role'), el.className]
      .filter(Boolean).join(' ');
  };
  const findInputs = () => [...document.querySelectorAll('input, textarea, [contenteditable="true"]')]
    .filter(el => !el.disabled && !el.readOnly);
  const allInputs = () => documents.flatMap(doc => [...doc.querySelectorAll('input, textarea, [contenteditable="true"]')])
    .filter(el => !el.disabled && !el.readOnly);
  const findKeywordInput = () => {
    const inputs = allInputs();
    return documents.map(doc => doc.querySelector('#rc_select_1')).find(Boolean)
      || inputs.find(el => /rc_select_1/.test(inputInfo(el)))
      || inputs.find(el => /keyword|key|search|query|关键词|搜索|候选人|职位|公司|行业|请输入/i.test(inputInfo(el)))
      || inputs.find(el => (el.getAttribute('role') || '') === 'combobox')
      || (document.activeElement && /input|textarea/i.test(document.activeElement.tagName) ? document.activeElement : null)
      || inputs[0];
  };
  const findSearchBox = () => {
    const candidates = documents.flatMap(doc => [...doc.querySelectorAll('*')])
      .filter(el => {
        const text = (el.innerText || el.textContent || '').trim();
        return text.includes('搜职位/公司/行业') || text.includes('中文用空格隔开');
      })
      .sort((a, b) => a.getBoundingClientRect().height - b.getBoundingClientRect().height);
    const textNode = candidates[0];
    return textNode ? (textNode.closest('.ant-select, [class*="select"], [role="combobox"]') || textNode) : null;
  };

  const searchBox = findSearchBox();
  if (searchBox) {
    searchBox.scrollIntoView({ block: 'center' });
    searchBox.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
    searchBox.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
    searchBox.click();
  }

  setTimeout(() => {
    const keywordInput = findKeywordInput();
    if (keywordInput) {
      keywordInput.focus();
    const nativeValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    if (nativeValueSetter && keywordInput instanceof HTMLInputElement) {
      nativeValueSetter.call(keywordInput, keywords);
    } else if (keywordInput.getAttribute('contenteditable') === 'true') {
      keywordInput.innerText = keywords;
    } else {
      keywordInput.value = keywords;
    }
    keywordInput.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: keywords }));
    keywordInput.dispatchEvent(new Event('change', { bubbles: true }));
    keywordInput.dispatchEvent(new KeyboardEvent('compositionend', { bubbles: true, data: keywords }));
    keywordInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true }));
    keywordInput.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true }));
    }

    const conditionHits = [];
    for (const word of conditionWords) {
      if (word && bodyText.includes(word)) conditionHits.push(word);
    }

    setTimeout(() => {
      const controls = documents.flatMap(doc => [...doc.querySelectorAll('button, a, [role="button"], .ant-btn')])
        .filter(visible);
      const searchButton = controls.find(el => /搜索|查询|找人/.test((el.innerText || el.textContent || '').trim()));
      if (searchButton) searchButton.click();
      else if (keywordInput) {
        keywordInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true }));
        keywordInput.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true }));
      }

      resolve({
        ok: Boolean(keywordInput),
        url: location.href,
        title: document.title,
        keywords,
        foundSearchBox: Boolean(searchBox),
        filledKeyword: Boolean(keywordInput),
        keywordInputInfo: keywordInput ? inputInfo(keywordInput) : '',
        keywordInputValue: keywordInput ? (keywordInput.value || keywordInput.innerText || '') : '',
        clickedSearch: Boolean(searchButton),
        conditionHits,
        documentCount: documents.length,
        visibleInputs: allInputs().slice(0, 12).map(inputInfo)
      });
    }, 250);
  }, 250);
}))();
"""


ROUTE_APPLY_CONDITIONS_JS = """
(() => new Promise(async resolve => {
  const payload = %s || {};
  const shouldClickSearch = Boolean(payload.click_search);
  const documents = [];
  const collectDocuments = doc => {
    if (!doc || documents.includes(doc)) return;
    documents.push(doc);
    for (const frame of [...doc.querySelectorAll('iframe')]) {
      try {
        if (frame.contentDocument) collectDocuments(frame.contentDocument);
      } catch (_) {}
    }
  };
  collectDocuments(document);
  try {

  const wait = ms => new Promise(done => setTimeout(done, ms));
  const visible = el => {
    if (!el || !el.getBoundingClientRect) return false;
    try {
      const rect = el.getBoundingClientRect();
      const view = (el.ownerDocument && el.ownerDocument.defaultView) || window;
      const style = view.getComputedStyle ? view.getComputedStyle(el) : window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
    } catch (_) {
      return false;
    }
  };
  const textOf = el => (el ? (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim() : '');
  const normalized = text => String(text || '').replace(/[\\s：:()（）]/g, '').trim().toLowerCase();
  const asList = value => {
    if (Array.isArray(value)) return value.map(v => String(v || '').trim()).filter(Boolean);
    if (typeof value === 'string') return value.split(/[，,\\s]+/).map(v => v.trim()).filter(Boolean);
    return [];
  };
  const clickLikeUser = el => {
    if (!el) return;
    el.scrollIntoView({ block: 'center', inline: 'center' });
    for (const eventName of ['pointerdown', 'mouseover', 'mousedown', 'mouseup', 'click']) {
      el.dispatchEvent(new MouseEvent(eventName, { bubbles: true, cancelable: true, view: window }));
    }
  };
  const setInputValue = (input, value) => {
    if (!input) return false;
    const text = String(value || '');
    input.focus();
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    if (setter && input instanceof HTMLInputElement) setter.call(input, text);
    else input.value = text;
    input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  };
  const allVisibleElements = () => documents.flatMap(doc => [...doc.querySelectorAll('*')]).filter(visible);
  const allVisibleInputs = () => documents.flatMap(doc => [...doc.querySelectorAll('input, textarea')]).filter(visible);
  const allVisibleButtons = () =>
    documents.flatMap(doc => [...doc.querySelectorAll('button, a, [role="button"], .ant-btn')]).filter(visible);
  const isDisabled = el => {
    if (!el) return true;
    const host = el.closest
      ? (el.closest('button, [role="button"], .ant-btn, [class*="btn"], [class*="button"]') || el)
      : el;
    return host.hasAttribute('disabled')
      || String(host.className || '').includes('disabled')
      || host.getAttribute('aria-disabled') === 'true';
  };
  const isTopMostElement = el => {
    if (!el || !el.getBoundingClientRect || !el.ownerDocument) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 1 || rect.height <= 1) return false;
    const doc = el.ownerDocument;
    const view = doc.defaultView || window;
    const x = Math.min(Math.max(rect.left + rect.width / 2, 1), Math.max(1, (view.innerWidth || 0) - 1));
    const y = Math.min(Math.max(rect.top + rect.height / 2, 1), Math.max(1, (view.innerHeight || 0) - 1));
    let top = null;
    try {
      top = doc.elementFromPoint(x, y);
    } catch (_) {
      top = null;
    }
    if (!top) return false;
    return top === el || el.contains(top) || top.contains(el);
  };
  const elementLooksActive = el => {
    if (!el) return false;
    const cls = String(el.className || '').toLowerCase();
    if (/(^|[-_\s])(active|selected|checked|cur|on|current)([-_\s]|$)/.test(cls)) return true;
    for (const attr of ['aria-checked', 'aria-selected', 'aria-pressed']) {
      const value = el.getAttribute(attr);
      if (value === 'true' || value === '1') return true;
    }
    const input = el.matches('input') ? el : el.querySelector('input');
    if (input && (input.checked || input.getAttribute('checked') !== null)) return true;
    return false;
  };
  const normalizeClickable = el =>
    (el && (el.closest('button, a, [role="button"], .ant-btn, [class*="btn"], [class*="button"]') || el)) || null;
  const uniqueElements = elements => {
    const used = new Set();
    const out = [];
    for (const el of elements) {
      if (!el || used.has(el)) continue;
      used.add(el);
      out.push(el);
    }
    return out;
  };
  const findInputById = inputId =>
    documents
      .map(doc => (doc.getElementById ? doc.getElementById(inputId) : doc.querySelector('#' + inputId)))
      .find(Boolean);
  const isConfirmText = text => /^(确认|确定)$/.test(String(text || '').replace(/\s+/g, '').trim());

  const findRowByTitle = title => {
    const target = normalized(title);
    const rows = allVisibleElements()
      .filter(el => el.classList && el.classList.contains('search-item'))
      .map(el => {
        const titleEl = el.querySelector('.search-item-title');
        const rowTitle = normalized(textOf(titleEl || el));
        return {
          el,
          rowTitle,
          rowText: normalized(textOf(el)),
          top: el.getBoundingClientRect().top,
        };
      })
      .filter(item => item.rowTitle || item.rowText)
      .sort((a, b) => a.top - b.top);
    const scoreRow = item => {
      const titleText = item.rowTitle || '';
      const rowText = item.rowText || '';
      if (!titleText && !rowText) return -1;
      let score = 0;
      if (titleText === target) score += 100;
      else if (titleText.startsWith(target) || titleText.endsWith(target)) score += 85;
      else if (titleText.includes(target)) score += 70;
      else if (rowText.includes(target)) score += 40;
      if (target === '语言' && titleText.includes('简历语言')) score -= 60;
      if (target === '职位' && /职位名称/.test(titleText)) score -= 20;
      score -= Math.max(0, titleText.length - target.length) * 0.4;
      return score;
    };
    const ranked = rows
      .map(item => ({ ...item, score: scoreRow(item) }))
      .sort((a, b) => b.score - a.score || a.top - b.top);
    if (ranked.length && ranked[0].score >= 50) return ranked[0].el;

    const marker = allVisibleElements()
      .map(el => ({ el, norm: normalized(textOf(el)), top: el.getBoundingClientRect().top }))
      .filter(item => item.norm && (item.norm === target || item.norm.includes(target) || target.includes(item.norm)))
      .sort((a, b) => a.norm.length - b.norm.length || a.top - b.top)[0];
    if (!marker) return null;
    return marker.el.closest('.search-item, [class*="search-item"], [class*="condition"], .ant-row')
      || marker.el.parentElement
      || null;
  };

  const pickBestOption = (targetValue, optionElements) => {
    const target = normalized(targetValue);
    const options = optionElements
      .map(el => ({ el, norm: normalized(textOf(el)), raw: textOf(el) }))
      .filter(item => item.norm);
    if (!options.length) return null;
    let matched = options.find(item => item.norm === target);
    if (matched) return matched.el;
    matched = options.find(item => item.norm.includes(target) || target.includes(item.norm));
    if (matched) return matched.el;
    const token = target.replace(/[^\\u4e00-\\u9fa5a-z0-9]/gi, '');
    if (token) {
      matched = options.find(item => item.norm.replace(/[^\\u4e00-\\u9fa5a-z0-9]/gi, '').includes(token));
      if (matched) return matched.el;
    }
    return null;
  };

  const activeDropdowns = () =>
    allVisibleElements()
      .filter(el => {
        const cls = String(el.className || '');
        if (!cls) return false;
        return /(ant-select-dropdown|search-component-suggest|suggest|autocomplete|dropdown)/i.test(cls);
      })
      .filter(el => !/hidden|hide/i.test(String(el.className || '')))
      .filter(el => (el.childElementCount || 0) > 0);

  const nearestDropdown = trigger => {
    const drops = activeDropdowns();
    if (!drops.length || !trigger) return null;
    const tr = trigger.getBoundingClientRect();
    return drops.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      const da = Math.abs(ra.left - tr.left) + Math.abs(ra.top - tr.bottom);
      const db = Math.abs(rb.left - tr.left) + Math.abs(rb.top - tr.bottom);
      return da - db;
    })[0];
  };

  const result = {
    url: location.href,
    title: document.title,
    documentCount: documents.length,
    applied: {},
    skipped: {},
    searchButtonFound: false,
    clickedSearch: false,
    verify: {},
  };

  const visibleSearchRows = () =>
    allVisibleElements()
      .filter(el => el.classList && el.classList.contains('search-item'))
      .filter(el => {
        const titleEl = el.querySelector('.search-item-title');
        return Boolean(textOf(titleEl || el));
      });

  const findAdvancedFilterToggle = () => {
    const controls = allVisibleButtons();
    return controls.find(el => {
      const txt = textOf(el).replace(/\s+/g, '');
      if (!txt || txt.length > 20) return false;
      if (/收起|隐藏|折叠/.test(txt)) return false;
      return /更多筛选|更多条件|高级筛选|展开筛选|展开更多|更多选项/.test(txt);
    });
  };

  const expandAdvancedFilters = async () => {
    const before = visibleSearchRows().length;
    let clicked = 0;
    for (let i = 0; i < 3; i += 1) {
      const toggle = findAdvancedFilterToggle();
      if (!toggle) break;
      clickLikeUser(toggle);
      clicked += 1;
      await wait(220);
      const afterLoop = visibleSearchRows().length;
      if (afterLoop > before) break;
    }
    return {
      before,
      after: visibleSearchRows().length,
      clicked,
    };
  };

  const clickOptionInDropdown = async (trigger, value) => {
    await wait(180);
    const dropdown = nearestDropdown(trigger);
    if (!dropdown) return false;
    const options = [...dropdown.querySelectorAll('.ant-select-item-option, [role="option"], li, a, div, span')]
      .filter(visible)
      .filter(el => {
        const txt = textOf(el);
        return txt && txt.length <= 120 && !/没找到相关匹配项|暂无数据|没有数据/.test(txt);
      });
    const option = pickBestOption(value, options);
    if (!option) return false;
    clickLikeUser(option);
    await wait(120);
    return true;
  };

  const setSelectByInputId = async (inputId, value) => {
    if (!value) return false;
    const input = findInputById(inputId);
    if (!input) return false;
    const trigger = input.closest('.ant-select') || input.parentElement || input;
    const selector = trigger.querySelector('.ant-select-selector') || trigger;
    clickLikeUser(selector);
    const ok = await clickOptionInDropdown(trigger, value);
    return ok;
  };

  const setAutocompleteByInputId = async (inputId, values) => {
    const list = asList(values);
    if (!list.length) return false;
    const input = findInputById(inputId);
    if (!input) return false;
    setInputValue(input, list.join(' '));
    await wait(120);
    return true;
  };

  const setInputNearLabel = (label, values) => {
    const list = asList(values);
    if (!list.length) return false;
    const row = findRowByTitle(label);
    if (!row) return false;
    const input = allVisibleInputs()
      .filter(el => {
        if (!row.contains(el)) return false;
        return !String(el.className || '').includes('search-component-input');
      })
      .sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left)[0]
      || row.querySelector('input, textarea');
    return setInputValue(input, list.join(' '));
  };

  const collectTagOptions = scope =>
    [...scope.querySelectorAll('label.tag-item, .tag-item, .unlimited-btn, .btn-choose, label.ant-checkbox-wrapper, .checkbox-item')]
      .filter(visible)
      .map(el => normalizeClickable(el) || el)
      .filter(Boolean)
      .filter(isTopMostElement)
      .map(el => ({ el, text: textOf(el), norm: normalized(textOf(el)), cls: String(el.className || '') }))
      .filter(item => item.text && item.text.length <= 20);

  const optionLooksActive = option => {
    if (!option || !option.el) return false;
    const cls = String(option.cls || '').toLowerCase();
    if (/(^|[-_\s])(active|selected|checked|cur|on|current)([-_\s]|$)/.test(cls)) return true;
    for (const attr of ['aria-checked', 'aria-selected', 'aria-pressed']) {
      const value = option.el.getAttribute(attr);
      if (value === 'true' || value === '1') return true;
    }
    const input = option.el.matches('input') ? option.el : option.el.querySelector('input');
    if (input && (input.checked || input.getAttribute('checked') !== null)) return true;
    return false;
  };

  const findTagScopeByLabel = (row, scopeLabel) => {
    if (!row || !scopeLabel) return row;
    const target = normalized(scopeLabel);
    const markers = [...row.querySelectorAll('*')]
      .map(el => ({ el, norm: normalized(textOf(el)), textLen: textOf(el).length }))
      .filter(item => item.norm && (item.norm === target || item.norm.includes(target) || target.includes(item.norm)))
      .sort((a, b) => a.textLen - b.textLen);
    for (const item of markers) {
      let scope = item.el;
      for (let i = 0; i < 7 && scope; i += 1) {
        if (collectTagOptions(scope).length >= 3) return scope;
        scope = scope.parentElement;
        if (scope === row) break;
      }
    }
    return row;
  };

  const collectRowTagSnapshot = (label, scopeLabel = '') => {
    const row = findRowByTitle(label);
    if (!row) return { found: false, active: [], options: [] };
    const scope = findTagScopeByLabel(row, scopeLabel || label);
    const options = collectTagOptions(scope).map(item => ({
      text: item.text,
      cls: item.cls,
      active: optionLooksActive(item),
    }));
    return {
      found: true,
      active: options.filter(item => item.active).map(item => item.text),
      options: options.slice(0, 14),
    };
  };

  const clickTagOptionsInRow = async (label, values, mapper, scopeLabel = '') => {
    const list = asList(values);
    if (!list.length) return { hit: 0, targetValues: [], activeValues: [], found: false };
    const row = findRowByTitle(label);
    if (!row) return { hit: 0, targetValues: [], activeValues: [], found: false };
    const scope = findTagScopeByLabel(row, scopeLabel || label);
    let hit = 0;
    const used = new Set();
    const targetValues = [];
    const collectFallbackOptions = () =>
      [...scope.querySelectorAll('span, a, label, div')]
        .filter(visible)
        .map(el => ({ el, text: textOf(el), norm: normalized(textOf(el)), cls: String(el.className || '') }))
        .filter(item => item.text && item.text.length <= 14 && !/[：:]/.test(item.text));
    for (const raw of list) {
      const value = mapper ? mapper(raw) : raw;
      if (!value) continue;
      targetValues.push(value);
      const target = normalized(value);
      const candidates = collectTagOptions(scope);
      let candidate = candidates.find(item => {
        if (used.has(item.el)) return false;
        return item.norm === target || item.norm.includes(target) || target.includes(item.norm);
      });
      if (!candidate) {
        const fallback = collectFallbackOptions();
        candidate = fallback.find(item => {
          if (used.has(item.el)) return false;
          return item.norm === target || item.norm.includes(target) || target.includes(item.norm);
        });
      }
      if (candidate) {
        used.add(candidate.el);
        clickLikeUser(candidate.el);
        hit += 1;
        await wait(110);
      }
    }
    const snapshot = collectRowTagSnapshot(label, scopeLabel);
    return {
      hit,
      targetValues,
      activeValues: snapshot.active || [],
      found: true,
    };
  };

  const valuesMatchTargets = (targets, activeValues) => {
    const targetList = asList(targets).map(normalized).filter(Boolean);
    if (!targetList.length) return false;
    const activeList = asList(activeValues).map(normalized).filter(Boolean);
    if (!activeList.length) return false;
    return targetList.some(target => activeList.some(active => active === target || active.includes(target) || target.includes(active)));
  };

  const mapCity = value => String(value || '').trim();

  result.verify = {
    ...(result.verify || {}),
    modalLogs: {},
  };
  const pushModalLog = (key, message) => {
    if (!result.verify.modalLogs[key]) result.verify.modalLogs[key] = [];
    result.verify.modalLogs[key].push(message);
  };

  const modalHeadersByTitle = titlePattern =>
    allVisibleElements()
      .filter(el => titlePattern.test(textOf(el)))
      .filter(el => {
        const txt = textOf(el);
        return txt && txt.length <= 40;
      })
      .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);

  const latestModalHeaderByTitle = titlePattern => {
    const headers = modalHeadersByTitle(titlePattern);
    return headers.length ? headers[headers.length - 1] : null;
  };

  const findModalRootFromHeader = header => {
    if (!header || !header.getBoundingClientRect) return null;
    const hr = header.getBoundingClientRect();
    const headerText = textOf(header);
    const isCityHeader = /请选择城市/.test(headerText);
    const isPositionHeader = /请选择职位类别/.test(headerText);
    const area = rect => Math.max(0, rect.width) * Math.max(0, rect.height);
    // 先沿祖先链找真正对话框容器，避免误选整个页面容器。
    let current = header;
    for (let i = 0; i < 12 && current; i += 1) {
      const rect = current.getBoundingClientRect ? current.getBoundingClientRect() : { width: 0, height: 0 };
      const cls = String(current.className || '');
      const role = String(current.getAttribute ? (current.getAttribute('role') || '') : '');
      const looksModal = /(ant-modal|rc-dialog|modal|dialog|drawer|layer|popup)/i.test(cls) || role === 'dialog';
      const hasConfirm = current.querySelectorAll
        ? [...current.querySelectorAll('button, a, span, div')].some(el => /^确认$/.test(textOf(el)))
        : false;
      const hasSelected = !isCityHeader || (current.querySelectorAll
        ? [...current.querySelectorAll('*')].some(el => /已选[（(]\s*\d+\s*\/\s*\d+\s*[）)]/.test(textOf(el)))
        : false);
      if (looksModal && hasConfirm && hasSelected && rect.width >= 420 && rect.height >= 220) return current;
      current = current.parentElement;
    }
    const candidates = allVisibleElements()
      .filter(el => el !== header && el.getBoundingClientRect && el.querySelectorAll)
      .map(el => {
        const rect = el.getBoundingClientRect();
        const containsHeader = rect.left <= hr.left + 4
          && rect.right >= hr.right - 4
          && rect.top <= hr.top + 4
          && rect.bottom >= hr.bottom - 4;
        if (!containsHeader || rect.width < 420 || rect.height < 220) return null;
        const txt = textOf(el);
        const cls = String(el.className || '');
        const hasModalSignal = /(modal|popup|dialog|drawer|mask|ant-modal|layer|rc-dialog)/i.test(cls)
          || /(请选择城市|请选择职位类别|热门城市|职位类别意见反馈|已选[（(]\s*\d+\s*\/\s*\d+\s*[）)]|确认|确定)/.test(txt);
        if (!hasModalSignal) return null;
        const hasConfirm = [...el.querySelectorAll('button, a, span, div')]
          .some(node => isConfirmText(textOf(node)));
        if (!hasConfirm) return null;
        if (isCityHeader && !/已选[（(]\s*\d+\s*\/\s*\d+\s*[）)]/.test(txt)) return null;
        if (isPositionHeader && !/职位类别|确认|确定/.test(txt)) return null;
        const zIndex = Number.parseInt((window.getComputedStyle(el).zIndex || '0'), 10);
        const zScore = Number.isFinite(zIndex) ? zIndex : 0;
        return { el, score: area(rect) - zScore * 6 };
      })
      .filter(Boolean)
      .sort((a, b) => a.score - b.score);
    return candidates[0]?.el || null;
  };

  const closestModalFromHeader = header => {
    if (!header) return null;
    const rootByHeader = findModalRootFromHeader(header);
    if (rootByHeader) return rootByHeader;
    let current = header;
    for (let i = 0; i < 8 && current; i += 1) {
      const cls = String(current.className || '');
      const looksLikeModal = /(modal|popup|dialog|layer|drawer|mask|ant-modal)/i.test(cls);
      const childCount = current.querySelectorAll ? current.querySelectorAll('*').length : 0;
      const hasConfirm = current.querySelectorAll
        ? [...current.querySelectorAll('button, a, span, div')].some(el => /^确认$/.test(textOf(el)))
        : false;
      if (looksLikeModal || (childCount > 40 && hasConfirm)) return current;
      current = current.parentElement;
    }
    current = header;
    for (let i = 0; i < 12 && current; i += 1) {
      const rect = current.getBoundingClientRect ? current.getBoundingClientRect() : { width: 0, height: 0 };
      const hasConfirm = current.querySelectorAll
        ? [...current.querySelectorAll('button, a, span, div')].some(el => /^确认$/.test(textOf(el)))
        : false;
      if (hasConfirm && rect.width > 420 && rect.height > 220) return current;
      current = current.parentElement;
    }
    return header.parentElement || header;
  };

  const findModalByTitle = titlePattern => {
    const header = latestModalHeaderByTitle(titlePattern);
    if (!header) return null;
    return closestModalFromHeader(header);
  };

  const waitForModal = async (titlePattern, timeoutMs = 1600) => {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const modal = findModalByTitle(titlePattern);
      if (modal && visible(modal)) return modal;
      await wait(80);
    }
    return null;
  };

  const waitForModalClose = async (titlePattern, timeoutMs = 1800) => {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const modal = findModalByTitle(titlePattern);
      if (!modal || !visible(modal)) return true;
      await wait(80);
    }
    return false;
  };

  const clickModalClose = async modal => {
    const modalContainer = modal || findModalByTitle(/请选择城市|请选择职位类别/);
    if (!modalContainer) return false;
    const header = latestModalHeaderByTitle(/请选择城市|请选择职位类别/);
    const scopedCloseBtn = [...modalContainer.querySelectorAll('button, a, span, i, div')]
      .filter(visible)
      .find(el => {
        const txt = textOf(el);
        const cls = String(el.className || '');
        return /^×$/.test(txt) || /关闭|取消/.test(txt) || /(close|icon-close|ant-modal-close-x)/i.test(cls);
      });
    const globalCloseCandidates = allVisibleElements()
      .filter(el => {
        const txt = textOf(el);
        const cls = String(el.className || '');
        return /^×$/.test(txt) || /关闭|取消/.test(txt) || /(close|icon-close|ant-modal-close-x)/i.test(cls);
      });
    const closeBtn = scopedCloseBtn || nearestElementToHeader(header, globalCloseCandidates);
    if (closeBtn) {
      clickLikeUser(closeBtn);
      await wait(120);
      return true;
    }
    for (let i = 0; i < 3; i += 1) {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }));
      document.dispatchEvent(new KeyboardEvent('keyup', { key: 'Escape', code: 'Escape', bubbles: true }));
      await wait(100);
    }
    return true;
  };

  const nearestElementToHeader = (header, elements) => {
    if (!header || !elements.length) return null;
    const hr = header.getBoundingClientRect();
    return elements
      .map(el => {
        const r = el.getBoundingClientRect();
        const dx = Math.abs((r.left + r.width / 2) - (hr.left + hr.width / 2));
        const dy = Math.abs((r.top + r.height / 2) - (hr.top + hr.height / 2));
        return { el, score: dx + dy * 1.1 };
      })
      .sort((a, b) => a.score - b.score)[0]?.el || null;
  };

  const closeAllModalsByTitle = async titlePattern => {
    let guard = 0;
    while (guard < 4) {
      const modal = findModalByTitle(titlePattern);
      if (!modal || !visible(modal)) return true;
      await clickModalClose(modal);
      const closed = await waitForModalClose(titlePattern, 800);
      if (closed) return true;
      guard += 1;
    }
    return false;
  };

  const clickRowEntry = async (row, label, keyForLog) => {
    if (!row) return false;
    const scope = findTagScopeByLabel(row, label);
    const isExpectedPosition = normalized(label) === normalized('期望职位');
    const targetNorm = normalized(label);
    const labelNode = [...row.querySelectorAll('*')]
      .filter(visible)
      .find(el => normalized(textOf(el)) === targetNorm);
    const labelTop = labelNode && labelNode.getBoundingClientRect ? labelNode.getBoundingClientRect().top : null;
    const rowTagOptions = collectTagOptions(row);
    const sameLineOptions = labelTop === null
      ? []
      : rowTagOptions.filter(item => {
        const r = item.el.getBoundingClientRect();
        return Math.abs(r.top - labelTop) <= 42;
      });
    const tagOptions = sameLineOptions.length ? sameLineOptions : (rowTagOptions.length ? rowTagOptions : collectTagOptions(scope));
    pushModalLog(
      keyForLog,
      `行入口候选：label=${label}；sameLine=${sameLineOptions.length}；row=${rowTagOptions.length}；scope=${collectTagOptions(scope).length}`
    );
    if (isExpectedPosition) {
      const triggerCandidates = [...row.querySelectorAll('button, a, input, span, i, div')]
        .filter(visible)
        .map(el => ({ el, txt: textOf(el), cls: String(el.className || '') }))
        .filter(item => /(choose|btn|select|dropdown|search-component|arrow|icon|combobox)/i.test(item.cls)
          || /(请选择职位类别|选择职位|展开|下拉)/.test(item.txt))
        .sort((a, b) => {
          const ra = a.el.getBoundingClientRect();
          const rb = b.el.getBoundingClientRect();
          return (rb.left + rb.width) - (ra.left + ra.width);
        });
      if (triggerCandidates.length) {
        clickLikeUser(triggerCandidates[0].el);
        pushModalLog(keyForLog, `点击期望职位入口：${triggerCandidates[0].txt || triggerCandidates[0].cls || 'trigger'}`);
        await wait(180);
        return true;
      }
    }
    const otherOption = tagOptions.find(item => /其他/.test(item.text));
    if (otherOption) {
      clickLikeUser(otherOption.el);
      pushModalLog(keyForLog, `点击行内入口：其他`);
      await wait(160);
      return true;
    }
    const candidates = [...scope.querySelectorAll('span, a, button, div, label')]
      .filter(visible)
      .map(el => ({ el, txt: textOf(el), cls: String(el.className || '') }))
      .filter(item => item.txt.length <= 16 || /(choose|btn|tag|select|dropdown|search-component)/i.test(item.cls));
    const target = candidates.find(item => /其他|请选择/.test(item.txt))
      || (!isExpectedPosition && candidates.find(item => /不限|全部/.test(item.txt)))
      || candidates.find(item => /(choose|btn|tag|select|dropdown|search-component)/i.test(item.cls))
      || null;
    if (target) {
      clickLikeUser(target.el);
      pushModalLog(keyForLog, `点击行内入口：${target.txt || target.cls || '候选元素'}`);
      await wait(160);
      return true;
    }
    clickLikeUser(scope);
    pushModalLog(keyForLog, `点击行区域兜底`);
    await wait(160);
    return true;
  };

  const retryOpenExpectedPositionModal = async row => {
    if (!row) return false;
    const candidates = [...row.querySelectorAll('button, a, input, span, i, div')]
      .filter(visible)
      .map(el => ({ el, txt: textOf(el), cls: String(el.className || '') }))
      .filter(item => /(choose|btn|select|dropdown|search-component|arrow|icon|combobox|ant-select)/i.test(item.cls)
        || /(请选择职位类别|期望职位|选择职位|展开|下拉)/.test(item.txt))
      .sort((a, b) => {
        const ra = a.el.getBoundingClientRect();
        const rb = b.el.getBoundingClientRect();
        const ar = ra.width * ra.height;
        const br = rb.width * rb.height;
        const rightA = ra.left + ra.width;
        const rightB = rb.left + rb.width;
        return (rightB - rightA) || (ar - br);
      });
    for (const item of candidates.slice(0, 4)) {
      clickLikeUser(item.el);
      await wait(180);
      const modal = findModalByTitle(/请选择职位类别/);
      if (modal && visible(modal)) {
        pushModalLog('expected_position_modal', `重试入口命中：${item.txt || item.cls || 'trigger'}`);
        return true;
      }
    }
    return false;
  };

  const chooseFromCityModal = async (modal, city, keyForLog) => {
    const header = latestModalHeaderByTitle(/请选择城市/);
    if (!modal && !header) return false;
    const modalContainer = modal || findModalByTitle(/请选择城市/);
    const modalRect = (() => {
      if (modalContainer && modalContainer.getBoundingClientRect) return modalContainer.getBoundingClientRect();
      if (header && header.getBoundingClientRect) return header.getBoundingClientRect();
      return null;
    })();
    const selectedCount = () => {
      const textPool = (modalContainer ? [...modalContainer.querySelectorAll('*')] : allVisibleElements())
        .map(el => textOf(el))
        .find(txt => /已选[（(]\s*\d+\s*\/\s*\d+\s*[）)]/.test(txt));
      const globalTextPool = allVisibleElements()
        .map(el => textOf(el))
        .find(txt => /已选[（(]\s*\d+\s*\/\s*\d+\s*[）)]/.test(txt));
      const rawText = textPool || globalTextPool || '';
      const match = rawText ? rawText.match(/已选[（(]\s*(\d+)\s*\/\s*\d+\s*[）)]/) : null;
      return match ? Number(match[1]) : -1;
    };
    const hasCityActive = targetCity => {
      const target = normalized(targetCity);
      const nodes = (modalContainer ? [...modalContainer.querySelectorAll('span, a, button, li, div')] : allVisibleElements())
        .filter(visible)
        .filter(el => {
          const txt = normalized(textOf(el));
          return txt && (txt === target || txt.includes(target) || target.includes(txt));
        });
      return nodes.some(node => elementLooksActive(node));
    };
    const hasEnabledConfirm = () => {
      const scoped = modalContainer
        ? [...modalContainer.querySelectorAll('button, a, [role="button"], .ant-btn, span, div')]
        : [];
      const global = allVisibleElements()
        .filter(el => isConfirmText(textOf(el)));
      const buttons = [...scoped, ...global]
        .filter(visible)
        .map(normalizeClickable)
        .filter(Boolean)
        .filter(el => isConfirmText(textOf(el)));
      return uniqueElements(buttons).some(el => !isDisabled(el));
    };
    const selectedBefore = selectedCount();
    if (selectedBefore >= 0) pushModalLog(keyForLog, `城市弹窗已选计数(前)：${selectedBefore}`);
    const collectCityOptions = includeGlobal => {
      const base = modalContainer ? [...modalContainer.querySelectorAll('span, a, button, li, div')] : [];
      const globals = includeGlobal ? allVisibleElements() : [];
      const pool = [...base, ...globals];
      return pool
        .filter(visible)
        .filter(el => {
          const txt = textOf(el);
          if (!txt || txt.length > 18 || /[：:]/.test(txt) || /请选择城市|热门城市|历史\/热门|国内|海外|加载中/.test(txt)) return false;
          const r = el.getBoundingClientRect();
          if (modalRect) {
            const strictInside = r.left >= modalRect.left + 8
              && r.right <= modalRect.right - 8
              && r.top >= modalRect.top + 26
              && r.bottom <= modalRect.bottom - 8;
            if (!strictInside) return false;
          }
          if (!includeGlobal || !modalRect) return isTopMostElement(el);
          const insideModal = r.left >= modalRect.left - 40
            && r.right <= modalRect.right + 40
            && r.top >= modalRect.top - 40
            && r.bottom <= modalRect.bottom + 220;
          if (!insideModal) return false;
          return isTopMostElement(el);
        })
        .map(normalizeClickable)
        .filter(Boolean)
        .filter(isTopMostElement);
    };
    const collectRankedCityCandidates = includeGlobal => {
      const target = normalized(city);
      const thresholdX = modalRect ? (modalRect.left + modalRect.width * 0.42) : -1;
      const candidates = collectCityOptions(includeGlobal)
        .map(el => {
          const txt = textOf(el);
          const norm = normalized(txt);
          if (!norm) return null;
          if (!(norm === target || norm.includes(target) || target.includes(norm))) return null;
          const rect = el.getBoundingClientRect();
          const cls = String(el.className || '');
          const centerX = rect.left + rect.width / 2;
          let score = 0;
          if (norm === target) score += 120;
          else score += 70;
          if (thresholdX > 0 && centerX >= thresholdX) score += 40;
          else if (thresholdX > 0) score -= 55;
          if (/(btn|tag|option|item|chip|city)/i.test(cls)) score += 18;
          if (rect.width >= 150 && rect.height >= 28) score -= 40;
          if (rect.width <= 140 && rect.height <= 56) score += 10;
          return { el, txt, score, rect, cls };
        })
        .filter(Boolean)
        .sort((a, b) => b.score - a.score);
      return uniqueElements(candidates.map(item => item.el));
    };
    const attemptSelect = async (candidate, sourceLabel) => {
      if (!candidate) return false;
      clickLikeUser(candidate);
      await wait(180);
      const selectedAfter = selectedCount();
      const activeHit = hasCityActive(city);
      const confirmEnabled = hasEnabledConfirm();
      pushModalLog(
        keyForLog,
        `城市候选点击[${sourceLabel}]：${textOf(candidate) || city}；已选=${selectedAfter >= 0 ? selectedAfter : '-'}；active=${activeHit ? 'Y' : 'N'}；confirm=${confirmEnabled ? 'Y' : 'N'}`
      );
      if (selectedBefore >= 0 && selectedAfter > selectedBefore) return true;
      if (activeHit) return true;
      if (confirmEnabled && selectedAfter > 0) return true;
      return false;
    };
    let picked = false;
    const localCandidates = collectRankedCityCandidates(false);
    for (const candidate of localCandidates.slice(0, 6)) {
      picked = await attemptSelect(candidate, 'local');
      if (picked) break;
    }
    if (!picked) {
      const globalCandidates = collectRankedCityCandidates(true);
      for (const candidate of globalCandidates.slice(0, 8)) {
        picked = await attemptSelect(candidate, 'global');
        if (picked) break;
      }
    }
    const allInputs = allVisibleInputs().filter(el => /搜索城市|请输入城市|搜索/.test(el.placeholder || ''));
    const scopedInputs = modalContainer
      ? [...modalContainer.querySelectorAll('input, textarea')].filter(visible).filter(el => /搜索城市|请输入城市|搜索/.test(el.placeholder || ''))
      : [];
    const searchInput = scopedInputs[0] || nearestElementToHeader(header, allInputs);
    if (!picked && searchInput) {
      setInputValue(searchInput, city);
      searchInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true }));
      searchInput.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true }));
      pushModalLog(keyForLog, `城市弹窗搜索：${city}`);
      const waitStart = Date.now();
      while (Date.now() - waitStart < 1500) {
        const loadingText = (modalContainer ? textOf(modalContainer) : '').replace(/\s+/g, '');
        const searchCandidates = collectRankedCityCandidates(true);
        if (searchCandidates.length) {
          picked = await attemptSelect(searchCandidates[0], 'search');
          if (picked) break;
        }
        const trigger = searchInput.closest('.ant-input-affix-wrapper, .ant-select, [role="combobox"], .search-component') || searchInput;
        const dropdownPicked = await clickOptionInDropdown(trigger, city);
        if (dropdownPicked) {
          await wait(140);
          picked = hasCityActive(city) || hasEnabledConfirm() || (selectedCount() > selectedBefore);
          pushModalLog(keyForLog, `搜索下拉选择：${dropdownPicked ? '命中' : '未命中'}；active=${hasCityActive(city) ? 'Y' : 'N'}`);
          if (picked) break;
        }
        if (!loadingText.includes('加载中')) await wait(120);
        else await wait(180);
      }
    }
    if (!picked) {
      pushModalLog(keyForLog, `城市弹窗未找到选项：${city}`);
      return false;
    }
    const selectedAfter = selectedCount();
    if (selectedAfter >= 0) pushModalLog(keyForLog, `城市弹窗已选计数(后)：${selectedAfter}`);
    if (selectedBefore >= 0 && selectedAfter >= 0 && selectedAfter <= selectedBefore && !hasCityActive(city)) {
      pushModalLog(keyForLog, `城市计数未增加，视为未选中：${city}`);
      return false;
    }
    return true;
  };

  const confirmModal = async (modal, keyForLog, titlePattern) => {
    const modalContainer = modal || findModalByTitle(titlePattern || /请选择城市|请选择职位类别/);
    const header = latestModalHeaderByTitle(titlePattern || /请选择城市|请选择职位类别/);
    const isCityModal = Boolean(titlePattern && String(titlePattern).includes('请选择城市'));
    const modalRect = modalContainer && modalContainer.getBoundingClientRect ? modalContainer.getBoundingClientRect() : null;
    const selectedCounter = isCityModal
      ? allVisibleElements()
        .filter(el => /已选[（(]\s*\d+\s*\/\s*\d+\s*[）)]/.test(textOf(el)))
        .sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top)[0] || null
      : null;
    const selectedRect = selectedCounter && selectedCounter.getBoundingClientRect ? selectedCounter.getBoundingClientRect() : null;
    const orderByHeader = elements =>
      elements
        .map(el => {
          if (!header) return { el, score: 999999 };
          const hr = header.getBoundingClientRect();
          const r = el.getBoundingClientRect();
          const dx = Math.abs((r.left + r.width / 2) - (hr.left + hr.width / 2));
          const dy = Math.abs((r.top + r.height / 2) - (hr.top + hr.height / 2));
          const rightBottomBoost = modalRect
            && r.left >= modalRect.left + modalRect.width * 0.5
            && r.top >= modalRect.top + modalRect.height * 0.55
            ? -280
            : 0;
          const footerBoost = el.closest('[class*="footer"], .ant-modal-footer') ? -220 : 0;
          return { el, score: dx + dy * 1.1 + rightBottomBoost + footerBoost };
        })
        .sort((a, b) => a.score - b.score)
        .map(item => item.el);
    const deadline = Date.now() + 2200;
    while (Date.now() < deadline) {
      const scopedButtons = modalContainer
        ? [...modalContainer.querySelectorAll('button, a, [role="button"], .ant-btn, span, div')]
          .filter(visible)
          .filter(isTopMostElement)
          .filter(el => isConfirmText(textOf(el)))
        : [];
      const globalButtons = allVisibleElements()
        .filter(el => isConfirmText(textOf(el)))
        .filter(isTopMostElement)
        .filter(el => {
          const r = el.getBoundingClientRect();
          return r.width >= 20 && r.height >= 16;
        });
      let candidates = uniqueElements([...scopedButtons, ...globalButtons]);
      if (isCityModal && selectedRect) {
        const nearFooter = candidates.filter(el => {
          const r = el.getBoundingClientRect();
          return r.top >= selectedRect.top - 90
            && r.bottom <= selectedRect.bottom + 120
            && r.left >= selectedRect.left - 40;
        });
        if (nearFooter.length) candidates = nearFooter;
      }
      const ordered = orderByHeader(candidates);
      const enabled = ordered.find(el => !isDisabled(el));
      if (enabled) {
        clickLikeUser(enabled);
        pushModalLog(keyForLog, `点击确认按钮：${textOf(enabled) || String(enabled.className || '')}`);
        await wait(180);
        return true;
      }
      if (ordered.length) {
        const btnPreview = ordered.slice(0, 3).map(el => textOf(el) || String(el.className || '')).join(' | ');
        pushModalLog(keyForLog, `确认按钮存在但未激活，继续等待：${btnPreview || '-'}`);
      }
      await wait(120);
    }
    pushModalLog(keyForLog, '确认按钮等待超时');
    return false;
  };

  const setCityByOtherPanel = async (label, values) => {
    const keyForLog = label === '目前城市' ? 'current_city_modal' : 'expected_city_modal';
    const targets = asList(values).map(mapCity).filter(Boolean);
    if (!targets.length) return { ok: false, hit: 0, reason: 'empty_values' };
    const row = findRowByTitle(label);
    if (!row) return { ok: false, hit: 0, reason: 'row_not_found' };
    await closeAllModalsByTitle(/请选择城市/);
    await clickRowEntry(row, label, keyForLog);
    let modal = await waitForModal(/请选择城市/);
    if (!modal) {
      pushModalLog(keyForLog, '未检测到城市弹窗');
      return { ok: false, hit: 0, reason: 'city_modal_not_found' };
    }
    pushModalLog(keyForLog, '检测到城市弹窗');
    let hit = 0;
    for (const city of targets) {
      const picked = await chooseFromCityModal(modal, city, keyForLog);
      if (picked) hit += 1;
      await wait(90);
    }
    const confirmed = await confirmModal(modal, keyForLog, /请选择城市/);
    if (!confirmed) return { ok: false, hit, reason: 'city_confirm_not_clicked' };
    let closedAfterConfirm = await waitForModalClose(/请选择城市/, 1200);
    if (!closedAfterConfirm) {
      pushModalLog(keyForLog, '确认后弹窗仍在，重试确认');
      await confirmModal(findModalByTitle(/请选择城市/), keyForLog, /请选择城市/);
      closedAfterConfirm = await waitForModalClose(/请选择城市/, 1200);
    }
    if (!closedAfterConfirm) {
      modal = findModalByTitle(/请选择城市/);
      if (modal && visible(modal)) {
        pushModalLog(keyForLog, '确认重试后弹窗仍在，尝试关闭');
        await clickModalClose(modal);
      }
    }
    let closed = await waitForModalClose(/请选择城市/);
    if (!closed) {
      await closeAllModalsByTitle(/请选择城市/);
      closed = await waitForModalClose(/请选择城市/, 1200);
    }
    pushModalLog(keyForLog, closed ? '城市弹窗已关闭' : '城市弹窗未关闭');
    return { ok: hit > 0 && closed, hit, reason: hit > 0 ? (closed ? '' : 'city_modal_not_closed') : 'city_not_found_in_modal' };
  };

  const setSchoolTagsStrict = async values => {
    const strict = await setTagOptionsStrict('院校要求', values, mapSchoolTag, '院校要求', 'school_tags', true);
    if (strict.ok) {
      return {
        ok: true,
        hit: strict.hit,
        targetValues: strict.targetValues || [],
        activeValues: strict.activeValues || [],
        reason: '',
      };
    }
    const fallback = await clickTagOptionsInRow('院校要求', values, mapSchoolTag, '院校要求');
    const snapshot = collectRowTagSnapshot('院校要求', '院校要求');
    return {
      ok: fallback.hit > 0 && valuesMatchTargets(asList(values).map(mapSchoolTag).filter(Boolean), snapshot.active || []),
      hit: strict.hit + fallback.hit,
      targetValues: strict.targetValues || asList(values).map(mapSchoolTag).filter(Boolean),
      activeValues: snapshot.active || [],
      reason: strict.reason || (fallback.hit > 0 ? '' : 'school_tag_not_found'),
    };
  };

  const setTagOptionsStrict = async (label, values, mapper, scopeLabel = '', logKey = '', requireAll = true) => {
    const mappedTargets = asList(values).map(item => (mapper ? mapper(item) : item)).filter(Boolean);
    if (!mappedTargets.length) return { ok: false, hit: 0, targetValues: [], activeValues: [], matched: 0, reason: 'empty_values' };
    const dedupTargets = [...new Set(mappedTargets)];
    const row = findRowByTitle(label);
    if (!row) return { ok: false, hit: 0, targetValues: dedupTargets, activeValues: [], matched: 0, reason: 'row_not_found' };
    const scope = findTagScopeByLabel(row, scopeLabel || label);
    const targetsNorm = dedupTargets.map(normalized);
    const hasUnlimitedTarget = targetsNorm.some(item => item === normalized('不限'));
    const safeLog = message => {
      if (!logKey) return;
      pushModalLog(logKey, message);
    };
    const matchCount = activeValues => {
      const activeNorms = asList(activeValues).map(normalized).filter(Boolean);
      return targetsNorm.filter(target => activeNorms.some(active => active === target || active.includes(target) || target.includes(active))).length;
    };
    const optionsBefore = collectTagOptions(scope);
    const unlimited = optionsBefore.find(item => normalized(item.text) === normalized('不限') && optionLooksActive(item));
    if (unlimited && !hasUnlimitedTarget) {
      clickLikeUser(unlimited.el);
      safeLog(`${label}：取消默认“不限”`);
      await wait(100);
    }
    let hit = 0;
    for (const target of dedupTargets) {
      const targetNorm = normalized(target);
      const options = collectTagOptions(scope);
      const candidate = options.find(item => {
        const itemNorm = normalized(item.text);
        return itemNorm === targetNorm || itemNorm.includes(targetNorm) || targetNorm.includes(itemNorm);
      });
      if (!candidate) {
        safeLog(`${label}：未找到标签 ${target}`);
        continue;
      }
      const alreadyActive = optionLooksActive(candidate);
      if (!alreadyActive) {
        clickLikeUser(candidate.el);
        await wait(100);
      }
      hit += 1;
      safeLog(`${label}：${alreadyActive ? '已激活' : '点击标签'} ${candidate.text || target}`);
    }
    const snapshot = collectRowTagSnapshot(label, scopeLabel || label);
    const matched = matchCount(snapshot.active || []);
    const enoughMatched = requireAll ? matched >= dedupTargets.length : matched > 0;
    const relaxedMatch = label === '工作年限' && hit > 0;
    return {
      ok: hit > 0 && (enoughMatched || relaxedMatch),
      hit,
      targetValues: dedupTargets,
      activeValues: snapshot.active || [],
      matched,
      reason: hit > 0 ? ((enoughMatched || relaxedMatch) ? '' : `strict_snapshot_miss_${matched}_${dedupTargets.length}`) : 'strict_no_click',
    };
  };

  const setExpectedPositionByCategoryModal = async values => {
    const targets = asList(values);
    if (!targets.length) return { ok: false, hit: 0, chosenValues: [], reason: 'empty_values' };
    const row = findRowByTitle('期望职位');
    if (!row) return { ok: false, hit: 0, chosenValues: [], reason: 'row_not_found' };
    await closeAllModalsByTitle(/请选择职位类别/);
    await clickRowEntry(row, '期望职位', 'expected_position_modal');
    let modal = await waitForModal(/请选择职位类别/);
    if (!modal) {
      const retried = await retryOpenExpectedPositionModal(row);
      if (retried) modal = await waitForModal(/请选择职位类别/);
    }
    if (!modal) {
      pushModalLog('expected_position_modal', '未检测到职位类别弹窗');
      return { ok: false, hit: 0, chosenValues: [], reason: 'position_modal_not_found' };
    }
    pushModalLog('expected_position_modal', '检测到职位类别弹窗');
    const chosenValues = [];
    let hit = 0;
    for (const value of targets) {
      const searchInput = [...modal.querySelectorAll('input, textarea')]
        .filter(visible)
        .find(el => /职位名称搜索|职位名称|请输入职位名称搜索|搜索/.test(el.placeholder || ''));
      if (searchInput) {
        setInputValue(searchInput, value);
        pushModalLog('expected_position_modal', `职位弹窗搜索：${value}`);
        await wait(280);
      }
      const optionNodes = [...modal.querySelectorAll('span, a, button, li, div')]
        .filter(visible)
        .filter(el => {
          const txt = textOf(el);
          return txt && txt.length <= 18 && !/[：:]/.test(txt) && !/请选择职位类别|职位类别意见反馈|请输入职位名称搜索/.test(txt);
        });
      let match = pickBestOption(value, optionNodes);
      if (!match) {
        const globalOptions = allVisibleElements()
          .filter(el => {
            const txt = textOf(el);
            return txt && txt.length <= 20 && !/[：:]/.test(txt) && !/请选择职位类别|职位类别意见反馈|请输入职位名称搜索/.test(txt);
          });
        match = pickBestOption(value, globalOptions);
      }
      if (!match) {
        pushModalLog('expected_position_modal', `职位弹窗未找到匹配：${value}`);
        continue;
      }
      clickLikeUser(match);
      const chosen = textOf(match) || value;
      chosenValues.push(chosen);
      pushModalLog('expected_position_modal', `选择职位：${chosen}`);
      hit += 1;
      await wait(120);
    }
    const confirmed = await confirmModal(modal, 'expected_position_modal', /请选择职位类别/);
    if (!confirmed) return { ok: false, hit, chosenValues: [...new Set(chosenValues)], reason: 'position_confirm_not_clicked' };
    const closedAfterConfirm = await waitForModalClose(/请选择职位类别/);
    if (!closedAfterConfirm) {
      modal = findModalByTitle(/请选择职位类别/);
      if (modal && visible(modal)) {
        pushModalLog('expected_position_modal', '确认后弹窗仍在，尝试关闭');
        await clickModalClose(modal);
      }
    }
    let closed = await waitForModalClose(/请选择职位类别/);
    if (!closed) {
      await closeAllModalsByTitle(/请选择职位类别/);
      closed = await waitForModalClose(/请选择职位类别/, 1200);
    }
    pushModalLog('expected_position_modal', closed ? '职位类别弹窗已关闭' : '职位类别弹窗未关闭');
    return {
      ok: hit > 0 && closed,
      hit,
      chosenValues: [...new Set(chosenValues)],
      reason: hit > 0 ? (closed ? '' : 'position_modal_not_closed') : 'position_not_found_in_modal',
    };
  };

  const findRowInput = row => {
    if (!row) return null;
    const candidates = [...row.querySelectorAll('input, textarea')]
      .filter(visible)
      .filter(el => !el.disabled && !el.readOnly)
      .sort((a, b) => {
        const ra = a.getBoundingClientRect();
        const rb = b.getBoundingClientRect();
        return (rb.width * rb.height) - (ra.width * ra.height);
      });
    return candidates[0] || null;
  };

  const setRowInputByTitle = async (label, values) => {
    const list = asList(values);
    if (!list.length) return { ok: false, hit: 0, reason: 'empty_values' };
    const row = findRowByTitle(label);
    if (!row) return { ok: false, hit: 0, reason: 'row_not_found' };
    const input = findRowInput(row);
    if (!input) return { ok: false, hit: 0, reason: 'input_not_found' };
    const trigger = input.closest('.ant-select, .search-component, [role="combobox"]') || row;
    let hit = 0;
    for (const value of list) {
      clickLikeUser(trigger);
      await wait(100);
      setInputValue(input, value);
      await wait(220);
      const picked = await clickOptionInDropdown(trigger, value);
      await wait(120);
      if (picked || normalized(textOf(row)).includes(normalized(value))) hit += 1;
    }
    return { ok: hit > 0, hit, reason: hit > 0 ? '' : 'typed_but_no_match' };
  };

  const toAge = value => {
    if (value === null || value === undefined || value === '') return null;
    const num = Number(String(value).replace(/岁/g, '').trim());
    if (!Number.isFinite(num)) return null;
    const age = Math.floor(num);
    return age >= 16 && age <= 80 ? age : null;
  };

  const collectAgeValuesFromRow = row => {
    if (!row) return [];
    const inputValues = [...row.querySelectorAll('input, textarea')]
      .filter(visible)
      .map(el => String(el.value || '').trim())
      .filter(Boolean);
    const textValues = (textOf(row).match(/\d{2}/g) || []).slice(0, 6);
    return [...new Set([...inputValues, ...textValues])];
  };

  const setAgeRangeByTitle = async (label, minAgeRaw, maxAgeRaw) => {
    const minAge = toAge(minAgeRaw);
    const maxAge = toAge(maxAgeRaw);
    if (!minAge && !maxAge) return { ok: false, reason: 'empty_age', values: [] };
    const row = findRowByTitle(label);
    if (!row) return { ok: false, reason: 'row_not_found', values: [] };
    const inputs = [...row.querySelectorAll('input, textarea')]
      .filter(visible)
      .filter(el => !el.disabled && !el.readOnly)
      .sort((a, b) => {
        const ra = a.getBoundingClientRect();
        const rb = b.getBoundingClientRect();
        return ra.left - rb.left || ra.top - rb.top;
      });
    const selectors = [...row.querySelectorAll('.ant-select, [role="combobox"], .search-component')]
      .filter(visible)
      .sort((a, b) => {
        const ra = a.getBoundingClientRect();
        const rb = b.getBoundingClientRect();
        return ra.left - rb.left || ra.top - rb.top;
      });
    let touched = 0;
    const fillInput = async (input, value) => {
      if (!input || !value) return false;
      const trigger = input.closest('.ant-select, .search-component, [role="combobox"]') || input;
      clickLikeUser(trigger);
      await wait(100);
      const ok = setInputValue(input, String(value));
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true }));
      input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true }));
      input.dispatchEvent(new Event('blur', { bubbles: true }));
      await wait(120);
      return ok;
    };
    if (inputs.length >= 2) {
      if (minAge && await fillInput(inputs[0], minAge)) touched += 1;
      if (maxAge && await fillInput(inputs[1], maxAge)) touched += 1;
    } else if (inputs.length === 1) {
      const rangeText = minAge && maxAge ? `${minAge}-${maxAge}` : String(minAge || maxAge);
      if (await fillInput(inputs[0], rangeText)) touched += 1;
    } else if (selectors.length >= 2) {
      if (minAge) {
        clickLikeUser(selectors[0].querySelector('.ant-select-selector') || selectors[0]);
        await wait(120);
        if (await clickOptionInDropdown(selectors[0], String(minAge))) touched += 1;
      }
      if (maxAge) {
        clickLikeUser(selectors[1].querySelector('.ant-select-selector') || selectors[1]);
        await wait(120);
        if (await clickOptionInDropdown(selectors[1], String(maxAge))) touched += 1;
      }
    }
    await wait(160);
    const values = collectAgeValuesFromRow(row);
    const matchedMin = !minAge || values.some(value => String(value).includes(String(minAge)));
    const matchedMax = !maxAge || values.some(value => String(value).includes(String(maxAge)));
    return {
      ok: touched > 0 && matchedMin && matchedMax,
      touched,
      inputCount: inputs.length,
      selectorCount: selectors.length,
      values,
      reason: touched > 0 ? (matchedMin && matchedMax ? '' : 'value_not_reflected') : 'no_writable_control',
    };
  };

  const collectRowInspector = (label, scopeLabel = '') => {
    const row = findRowByTitle(label);
    if (!row) {
      return {
        found: false,
        rowText: '',
        inputs: [],
        options: [],
      };
    }
    const scope = findTagScopeByLabel(row, scopeLabel || label);
    const inputs = [...row.querySelectorAll('input, textarea')]
      .filter(visible)
      .map(el => ({
        id: el.id || '',
        name: el.name || '',
        cls: String(el.className || ''),
        placeholder: el.placeholder || '',
        value: (el.value || '').slice(0, 80),
      }))
      .slice(0, 10);
    const options = [...new Set(
      collectTagOptions(scope).map(item => item.text).filter(Boolean)
    )].slice(0, 20);
    return {
      found: true,
      rowText: textOf(row).slice(0, 220),
      inputs,
      options,
    };
  };

  const setSelectByIdWithMapper = async (inputId, rawValue, mapper) => {
    const value = mapper ? mapper(rawValue) : rawValue;
    if (!value) return false;
    return setSelectByInputId(inputId, value);
  };

  const setTypedSelectByInputId = async (inputId, values) => {
    const list = asList(values);
    if (!list.length) return false;
    const input = findInputById(inputId);
    if (!input) return false;
    const trigger = input.closest('.ant-select') || input.parentElement || input;
    const selector = trigger.querySelector('.ant-select-selector') || trigger;
    let any = false;
    for (const value of list) {
      clickLikeUser(selector);
      await wait(120);
      setInputValue(input, value);
      await wait(240);
      const picked = await clickOptionInDropdown(trigger, value);
      await wait(120);
      any = any || picked || normalized(textOf(trigger)).includes(normalized(value));
    }
    return any;
  };

  const setSearchComponentInputByTitle = async (label, values) => {
    const list = asList(values);
    if (!list.length) return false;
    const row = findRowByTitle(label);
    if (!row) return false;
    const input = row.querySelector('input.search-component-input, input.ant-input.search-component-input')
      || [...row.querySelectorAll('input')]
        .filter(visible)
        .find(el => !el.disabled && !el.readOnly);
    if (!input) return false;
    const keyword = list[0];
    setInputValue(input, keyword);
    await wait(220);
    const suggest = row.querySelector('.search-component-suggest:not(.hide), .search-component-suggest');
    if (suggest && visible(suggest)) {
      const clickables = [...suggest.querySelectorAll('a, li, div, span')]
        .filter(visible)
        .filter(el => {
          const txt = textOf(el);
          return txt && txt.length <= 80 && !txt.includes('没找到相关匹配项');
        });
      const picked = pickBestOption(keyword, clickables) || clickables[0];
      if (picked) clickLikeUser(picked);
    }
    await wait(120);
    if (Boolean((input.value || '').trim() || normalized(textOf(row)).includes(normalized(keyword)))) return true;
    const typedFallback = await setRowInputByTitle(label, list);
    return Boolean(typedFallback.ok);
  };

  const mapRecruitType = value => {
    const text = normalized(value);
    if (!text) return '';
    if (text.includes('本科')) return '统招本科';
    if (text.includes('硕士')) return '统招硕士';
    if (text.includes('博士')) return '统招博士';
    if (text.includes('大专')) return '统招大专';
    if (text.includes('不限') || text.includes('统招非统招')) return '统招/非统招（不限）';
    return String(value || '').trim();
  };
  const mapWorkYear = value => {
    const text = normalized(value);
    if (!text) return '';
    if (text.includes('应届')) return '应届生';
    if (text.includes('13')) return '1-3年';
    if (text.includes('35')) return '3-5年';
    if (text.includes('510')) return '5-10年';
    if (text.includes('10') && text.includes('以上')) return '10年以上';
    if (text.includes('不限')) return '不限';
    return String(value || '').trim();
  };
  const mapEducation = value => {
    const text = normalized(value);
    if (!text) return '';
    if (text.includes('博士')) return '博士/博士后';
    if (text.includes('本科')) return '本科';
    if (text.includes('硕士')) return '硕士';
    if (text.includes('大专')) return '大专';
    if (text.includes('中专') || text.includes('中技')) return '中专/中技';
    if (text.includes('高中')) return '高中及以下';
    if (text.includes('不限')) return '不限';
    return String(value || '').trim();
  };
  const mapSchoolTag = value => {
    const text = normalized(value);
    if (!text) return '';
    if (text.includes('双一流')) return '双一流';
    if (text.includes('海外')) return '海外留学';
    if (text.includes('211')) return '211';
    if (text.includes('985')) return '985';
    if (text.includes('不限')) return '不限';
    return String(value || '').trim();
  };

  const normalizeWorkYearsForApply = values => {
    const list = asList(values).map(mapWorkYear).filter(Boolean);
    if (!list.length) return [];
    const rank = {
      '应届生': 0,
      '1-3年': 1,
      '3-5年': 2,
      '5-10年': 3,
      '10年以上': 4,
      '不限': 5,
      '自定义': 6,
    };
    return [...new Set(list)].sort((a, b) => (rank[a] ?? 99) - (rank[b] ?? 99));
  };

  const pickWorkYearForSingleSelect = values => {
    const list = normalizeWorkYearsForApply(values);
    if (!list.length) return [];
    const preferred = list.filter(item => item !== '不限' && item !== '自定义');
    if (!preferred.length) return [list[0]];
    return [preferred[preferred.length - 1]];
  };

  const normalizeEducationForApply = values => {
    const list = asList(values).map(mapEducation).filter(Boolean);
    if (!list.length) return [];
    const rank = {
      '高中及以下': 0,
      '中专/中技': 1,
      '大专': 2,
      '本科': 3,
      '硕士': 4,
      '博士/博士后': 5,
      '不限': 6,
    };
    return [...new Set(list)].sort((a, b) => (rank[a] ?? 99) - (rank[b] ?? 99));
  };

  const assign = (key, ok, detail) => {
    if (ok) result.applied[key] = detail || true;
    else result.skipped[key] = detail || '未找到对应控件';
  };
  const stepDelayRaw = Number(payload.step_delay_ms);
  const stepDelayMs = Number.isFinite(stepDelayRaw) ? Math.max(0, Math.min(5000, Math.floor(stepDelayRaw))) : 0;
  const stepTrace = [];
  let stepIndex = 0;
  const waitStep = async () => {
    if (stepDelayMs > 0) await wait(Math.round(stepDelayMs * (0.75 + Math.random() * 0.6)));
  };
  const recordStep = (key, durationMs = 0) => {
    const ok = Object.prototype.hasOwnProperty.call(result.applied, key);
    const detail = ok ? result.applied[key] : result.skipped[key];
    stepIndex += 1;
    stepTrace.push({
      index: stepIndex,
      field: key,
      status: ok ? 'applied' : 'skipped',
      detail: String(detail || ''),
      durationMs: Math.max(0, Number(durationMs) || 0),
    });
  };
  const assignWithTrace = async (key, ok, detail, durationMs = 0) => {
    assign(key, ok, detail);
    recordStep(key, durationMs);
    await waitStep();
  };
  const skipWithTrace = async (key, reason = '无输入') => {
    result.skipped[key] = reason;
    recordStep(key, 0);
  };

  const expandInfo = await expandAdvancedFilters();
  result.verify.searchItemCountBefore = expandInfo.before;
  result.verify.searchItemCountAfter = expandInfo.after;
  result.verify.expandToggleClicked = expandInfo.clicked;
  result.verify.visibleRowTitles = visibleSearchRows()
    .map(el => textOf(el.querySelector('.search-item-title') || el))
    .filter(Boolean)
    .slice(0, 40);

  const keywordList = asList(payload.keywords);
  const positionKeywordList = asList(payload.position_keywords);
  const companyKeywordList = asList(payload.company_keywords);
  const schoolTagList = asList(payload.school_tags);
  const languageList = asList(payload.languages);
  const expectedIndustryList = asList(payload.expected_industry);
  const expectedPositionList = asList(payload.expected_position);
  const schoolList = asList(payload.schools);
  const majorList = asList(payload.majors);

  if (payload.keyword_match) {
    const started = Date.now();
    const ok = await setSelectByInputId('rc_select_0', payload.keyword_match);
    await assignWithTrace('keyword_match', ok, payload.keyword_match || '', Date.now() - started);
  } else {
    await skipWithTrace('keyword_match');
  }
  if (keywordList.length) {
    const started = Date.now();
    const ok = await setAutocompleteByInputId('rc_select_1', keywordList);
    await assignWithTrace('keywords', ok, keywordList.join(' '), Date.now() - started);
  } else {
    await skipWithTrace('keywords');
  }
  if (positionKeywordList.length) {
    const started = Date.now();
    const ok = await setAutocompleteByInputId('rc_select_2', positionKeywordList);
    await assignWithTrace('position_keywords', ok, positionKeywordList.join(' '), Date.now() - started);
  } else {
    await skipWithTrace('position_keywords');
  }
  if (companyKeywordList.length) {
    const started = Date.now();
    const ok = await setAutocompleteByInputId('rc_select_4', companyKeywordList);
    await assignWithTrace('company_keywords', ok, companyKeywordList.join(' '), Date.now() - started);
  } else {
    await skipWithTrace('company_keywords');
  }

  const currentCityList = asList(payload.current_city);
  if (currentCityList.length) {
    const currentCityResult = await clickTagOptionsInRow('目前城市', currentCityList, null, '目前城市');
    const currentCityFallback = currentCityResult.hit > 0 && valuesMatchTargets(currentCityList, currentCityResult.activeValues)
      ? { ok: true, hit: 0, reason: '' }
      : await setRowInputByTitle('目前城市', currentCityList);
    const currentCityOtherPanel = currentCityResult.hit > 0 && valuesMatchTargets(currentCityList, currentCityResult.activeValues)
      ? { ok: true, hit: 0, reason: '' }
      : await setCityByOtherPanel('目前城市', currentCityList);
    const currentCityOk = currentCityResult.hit > 0 || currentCityFallback.ok || currentCityOtherPanel.ok;
    await assignWithTrace(
      'current_city',
      currentCityOk,
      currentCityOk
        ? `标签点击${currentCityResult.hit}项；激活：${(currentCityResult.activeValues || []).join('、') || '-'}；输入兜底：${currentCityFallback.ok ? `命中${currentCityFallback.hit}` : '未触发/失败'}；其他面板：${currentCityOtherPanel.ok ? `命中${currentCityOtherPanel.hit}` : '失败'}`
        : `未找到可点击城市标签；输入兜底失败：${currentCityFallback.reason || 'unknown'}；其他面板失败：${currentCityOtherPanel.reason || 'unknown'}`,
      0
    );
  } else {
    await skipWithTrace('current_city');
  }
  const expectedCityList = asList(payload.expected_city);
  if (expectedCityList.length) {
    const expectedCityResult = await clickTagOptionsInRow('期望城市', expectedCityList, null, '期望城市');
    const expectedCityFallback = expectedCityResult.hit > 0 && valuesMatchTargets(expectedCityList, expectedCityResult.activeValues)
      ? { ok: true, hit: 0, reason: '' }
      : await setRowInputByTitle('期望城市', expectedCityList);
    const expectedCityOtherPanel = expectedCityResult.hit > 0 && valuesMatchTargets(expectedCityList, expectedCityResult.activeValues)
      ? { ok: true, hit: 0, reason: '' }
      : await setCityByOtherPanel('期望城市', expectedCityList);
    const expectedCityOk = expectedCityResult.hit > 0 || expectedCityFallback.ok || expectedCityOtherPanel.ok;
    await assignWithTrace(
      'expected_city',
      expectedCityOk,
      expectedCityOk
        ? `标签点击${expectedCityResult.hit}项；激活：${(expectedCityResult.activeValues || []).join('、') || '-'}；输入兜底：${expectedCityFallback.ok ? `命中${expectedCityFallback.hit}` : '失败'}；其他面板：${expectedCityOtherPanel.ok ? `命中${expectedCityOtherPanel.hit}` : '失败'}`
        : `未找到可点击城市标签；输入兜底失败：${expectedCityFallback.reason || 'unknown'}；其他面板失败：${expectedCityOtherPanel.reason || 'unknown'}`,
      0
    );
  } else {
    await skipWithTrace('expected_city');
  }
  if (payload.recruit_type) {
    const started = Date.now();
    const ok = await setSelectByIdWithMapper('rc_select_6', payload.recruit_type, mapRecruitType);
    await assignWithTrace('recruit_type', ok, payload.recruit_type || '', Date.now() - started);
  } else {
    await skipWithTrace('recruit_type');
  }

  const workYearList = asList(payload.work_years);
  const workYearTargetsForApply = pickWorkYearForSingleSelect(workYearList);
  result.verify.workYearTargetsForApply = workYearTargetsForApply;
  if (workYearList.length) {
    const normalizedWorkYears = normalizeWorkYearsForApply(workYearList);
    const strictWorkYearResult = await setTagOptionsStrict('工作年限', workYearTargetsForApply, null, '工作年限', 'work_years', true);
    const workYearFallbackResult = strictWorkYearResult.ok
      ? { hit: 0, targetValues: [], activeValues: strictWorkYearResult.activeValues, found: true, reason: '' }
      : await clickTagOptionsInRow('工作年限', workYearTargetsForApply, null, '工作年限');
    result.verify.workYearDirectHit = Math.max(strictWorkYearResult.hit || 0, workYearFallbackResult.hit || 0);
    const finalWorkYearActive = (strictWorkYearResult.activeValues && strictWorkYearResult.activeValues.length)
      ? strictWorkYearResult.activeValues
      : (workYearFallbackResult.activeValues || []);
    const activeNorms = finalWorkYearActive.map(normalized);
    const matchedActiveCount = workYearTargetsForApply.filter(value => {
      const target = normalized(value);
      return activeNorms.some(item => item === target || item.includes(target) || target.includes(item));
    }).length;
    const workYearOk = strictWorkYearResult.ok || workYearFallbackResult.hit > 0;
    await assignWithTrace(
      'work_years',
      workYearOk,
      workYearOk
        ? `尝试：${workYearTargetsForApply.join('、')}（原始：${normalizedWorkYears.join('、') || workYearList.join('、')}；工作年限为单选，按高匹配优先；激活：${finalWorkYearActive.join('、') || '-'}；命中：${matchedActiveCount}/${workYearTargetsForApply.length}；严格模式：${strictWorkYearResult.ok ? `命中${strictWorkYearResult.matched}` : strictWorkYearResult.reason || '失败'}）`
        : `未找到年限标签（目标：${workYearTargetsForApply.join('、') || normalizedWorkYears.join('、') || workYearList.join('、')}；严格模式：${strictWorkYearResult.reason || '失败'}）`,
      0
    );
  } else {
    await skipWithTrace('work_years');
  }
  const educationList = asList(payload.education);
  if (educationList.length) {
    const normalizedEducation = normalizeEducationForApply(educationList);
    const strictEducationResult = await setTagOptionsStrict('教育经历', normalizedEducation, null, '教育经历', 'education');
    const educationFallbackResult = strictEducationResult.ok
      ? { hit: 0, targetValues: [], activeValues: strictEducationResult.activeValues, found: true, reason: '' }
      : await clickTagOptionsInRow('教育经历', normalizedEducation, null, '教育经历');
    const finalEducationActive = (strictEducationResult.activeValues && strictEducationResult.activeValues.length)
      ? strictEducationResult.activeValues
      : (educationFallbackResult.activeValues || []);
    const activeNorms = finalEducationActive.map(normalized);
    const matchedActiveCount = normalizedEducation.filter(value => {
      const target = normalized(value);
      return activeNorms.some(item => item === target || item.includes(target) || target.includes(item));
    }).length;
    const educationOk = strictEducationResult.ok || educationFallbackResult.hit > 0;
    await assignWithTrace(
      'education',
      educationOk,
      educationOk
        ? `尝试：${normalizedEducation.join('、')}（原始：${educationList.join('、')}；激活：${finalEducationActive.join('、') || '-'}；命中：${matchedActiveCount}/${normalizedEducation.length}；严格模式：${strictEducationResult.ok ? `命中${strictEducationResult.matched}` : strictEducationResult.reason || '失败'}）`
        : `未找到学历标签（目标：${normalizedEducation.join('、') || educationList.join('、')}；严格模式：${strictEducationResult.reason || '失败'}）`,
      0
    );
  } else {
    await skipWithTrace('education');
  }
  {
    const ageMin = toAge(payload.age_min);
    const ageMax = toAge(payload.age_max);
    if (ageMin || ageMax) {
      const started = Date.now();
      const ageResult = await setAgeRangeByTitle('年龄', ageMin, ageMax);
      result.verify.age = ageResult;
      const detail = ageResult.ok
        ? `目标：${ageMin || '-'}-${ageMax || '-'}；控件：input=${ageResult.inputCount || 0}, select=${ageResult.selectorCount || 0}；回读：${(ageResult.values || []).join('、') || '-'}`
        : `年龄写入失败：${ageResult.reason || 'unknown'}；目标：${ageMin || '-'}-${ageMax || '-'}；控件：input=${ageResult.inputCount || 0}, select=${ageResult.selectorCount || 0}；回读：${(ageResult.values || []).join('、') || '-'}`;
      if (ageMin) {
        await assignWithTrace('age_min', ageResult.ok, detail, Date.now() - started);
      } else {
        await skipWithTrace('age_min');
      }
      if (ageMax) {
        await assignWithTrace('age_max', ageResult.ok, detail, 0);
      } else {
        await skipWithTrace('age_max');
      }
    } else {
      await skipWithTrace('age_min');
      await skipWithTrace('age_max');
    }
  }
  if (schoolTagList.length) {
    const schoolTagResult = await setSchoolTagsStrict(schoolTagList);
    const schoolTagOk = schoolTagResult.ok;
    await assignWithTrace(
      'school_tags',
      schoolTagOk,
      schoolTagOk
        ? `标签点击${schoolTagResult.hit}项；目标：${(schoolTagResult.targetValues || []).join('、') || '-'}；激活：${(schoolTagResult.activeValues || []).join('、') || '-'}`
        : `院校标签未命中：${schoolTagResult.reason || 'unknown'}；目标：${(schoolTagResult.targetValues || []).join('、') || '-'}；激活：${(schoolTagResult.activeValues || []).join('、') || '-'}`,
      0
    );
  } else {
    await skipWithTrace('school_tags');
  }

  if (payload.active_days) {
    const started = Date.now();
    const ok = await setSelectByInputId('rc_select_8', payload.active_days);
    await assignWithTrace('active_days', ok, payload.active_days || '', Date.now() - started);
  } else {
    await skipWithTrace('active_days');
  }
  if (payload.gender) {
    const started = Date.now();
    const ok = await setSelectByInputId('rc_select_9', payload.gender);
    await assignWithTrace('gender', ok, payload.gender || '', Date.now() - started);
  } else {
    await skipWithTrace('gender');
  }
  if (payload.job_hopping) {
    const started = Date.now();
    const ok = await setSelectByInputId('rc_select_10', payload.job_hopping);
    await assignWithTrace('job_hopping', ok, payload.job_hopping || '', Date.now() - started);
  } else {
    await skipWithTrace('job_hopping');
  }
  if (payload.resume_language) {
    const started = Date.now();
    const ok = await setSelectByInputId('rc_select_15', payload.resume_language);
    await assignWithTrace('resume_language', ok, payload.resume_language || '', Date.now() - started);
  } else {
    await skipWithTrace('resume_language');
  }

  if (languageList.length) {
    const languageResult = await clickTagOptionsInRow('语言', languageList, null, '语言');
    const languageFallback = languageResult.hit > 0 && valuesMatchTargets(languageList, languageResult.activeValues)
      ? { ok: true, hit: 0, reason: '' }
      : await setRowInputByTitle('语言', languageList);
    const languageOk = languageResult.hit > 0 || languageFallback.ok;
    await assignWithTrace(
      'languages',
      languageOk,
      languageOk
        ? `标签点击${languageResult.hit}项；激活：${(languageResult.activeValues || []).join('、') || '-'}；输入兜底：${languageFallback.ok ? `命中${languageFallback.hit}` : '未触发/失败'}`
        : `未找到语言标签；输入兜底失败：${languageFallback.reason || 'unknown'}`,
      0
    );
  } else {
    await skipWithTrace('languages');
  }

  const currentIndustryList = asList(payload.current_industry);
  if (currentIndustryList.length) {
    const started = Date.now();
    const industrySet = await setTypedSelectByInputId('rc_select_7', currentIndustryList);
    await assignWithTrace('current_industry', industrySet, currentIndustryList.join('，'), Date.now() - started);
  } else {
    await skipWithTrace('current_industry');
  }
  if (expectedIndustryList.length) {
    const started = Date.now();
    const expectedIndustrySet = await setTypedSelectByInputId('rc_select_11', expectedIndustryList);
    await assignWithTrace('expected_industry', expectedIndustrySet, expectedIndustryList.join('，'), Date.now() - started);
  } else {
    await skipWithTrace('expected_industry');
  }

  const currentPositionList = asList(payload.current_position);
  if (currentPositionList.length) {
    const started = Date.now();
    const currentPositionSet = await setSearchComponentInputByTitle('当前职位', currentPositionList);
    await assignWithTrace('current_position', currentPositionSet, currentPositionList.join('，'), Date.now() - started);
  } else {
    await skipWithTrace('current_position');
  }
  if (expectedPositionList.length) {
    const expectedPositionModalResult = await setExpectedPositionByCategoryModal(expectedPositionList);
    const expectedPositionSet = expectedPositionModalResult.ok
      || await setSearchComponentInputByTitle('期望职位', expectedPositionList);
    const expectedPositionFallback = expectedPositionSet ? { ok: true, hit: 0, reason: '' } : await setRowInputByTitle('期望职位', expectedPositionList);
    const finalExpectedTargets = expectedPositionModalResult.chosenValues && expectedPositionModalResult.chosenValues.length
      ? expectedPositionModalResult.chosenValues
      : expectedPositionList;
    result.verify.expectedPositionFinalTargets = finalExpectedTargets;
    await assignWithTrace(
      'expected_position',
      expectedPositionSet || expectedPositionFallback.ok,
      expectedPositionSet || expectedPositionFallback.ok
        ? `目标：${expectedPositionList.join('，')}；面板命中：${expectedPositionModalResult.hit || 0}；面板选择：${(expectedPositionModalResult.chosenValues || []).join('、') || '-'}；输入兜底：${expectedPositionFallback.ok ? `命中${expectedPositionFallback.hit}` : '未触发/失败'}`
        : `目标：${expectedPositionList.join('，')}；面板失败：${expectedPositionModalResult.reason || 'unknown'}；输入兜底失败：${expectedPositionFallback.reason || 'unknown'}`,
      0
    );
  } else {
    await skipWithTrace('expected_position');
  }

  if (schoolList.length) {
    const started = Date.now();
    const ok = await setAutocompleteByInputId('rc_select_12', schoolList);
    await assignWithTrace('schools', ok, schoolList.join('，'), Date.now() - started);
  } else {
    await skipWithTrace('schools');
  }
  if (majorList.length) {
    const started = Date.now();
    const ok = await setAutocompleteByInputId('rc_select_13', majorList);
    await assignWithTrace('majors', ok, majorList.join('，'), Date.now() - started);
  } else {
    await skipWithTrace('majors');
  }
  const jobStatusList = asList(payload.job_status);
  if (jobStatusList.length) {
    const started = Date.now();
    const ok = await setTypedSelectByInputId('rc_select_14', jobStatusList);
    await assignWithTrace('job_status', ok, jobStatusList.join('，'), Date.now() - started);
  } else {
    await skipWithTrace('job_status');
  }

  const finalTagSnapshots = {
    current_city: collectRowTagSnapshot('目前城市', '目前城市'),
    expected_city: collectRowTagSnapshot('期望城市', '期望城市'),
    work_years: collectRowTagSnapshot('工作年限', '工作年限'),
    education: collectRowTagSnapshot('教育经历', '教育经历'),
    school_tags: collectRowTagSnapshot('院校要求', '院校要求'),
    languages: collectRowTagSnapshot('语言', '语言'),
  };
  const finalRowTexts = {
    current_city: normalized(textOf(findRowByTitle('目前城市'))),
    expected_city: normalized(textOf(findRowByTitle('期望城市'))),
  };
  const reconcileWithSnapshot = (field, targets, mapper) => {
    const list = asList(targets).map(item => (mapper ? mapper(item) : item)).filter(Boolean);
    if (!list.length) return;
    if (field === 'work_years' && Number(result.verify.workYearDirectHit || 0) > 0) {
      if (!Object.prototype.hasOwnProperty.call(result.applied, field)) {
        result.applied[field] = `直接命中：${list.join('、')}`;
      }
      return;
    }
    const snapshot = finalTagSnapshots[field] || {};
    const rowText = String(finalRowTexts[field] || '');
    const matchedByRowText = list
      .map(normalized)
      .filter(Boolean)
      .some(target => rowText.includes(target));
    const matched = valuesMatchTargets(list, snapshot.active || []) || matchedByRowText;
    if (!matched) {
      delete result.applied[field];
      result.skipped[field] = `最终快照未命中（目标：${list.join('、') || '-'}；激活：${asList(snapshot.active).join('、') || '-'})`;
      return;
    }
    if (!Object.prototype.hasOwnProperty.call(result.applied, field)) {
      result.applied[field] = `最终快照命中：${asList(snapshot.active).join('、') || '-'}`;
    }
  };
  reconcileWithSnapshot('current_city', currentCityList);
  reconcileWithSnapshot('expected_city', expectedCityList);
  reconcileWithSnapshot('work_years', workYearTargetsForApply);
  reconcileWithSnapshot('education', educationList, mapEducation);
  reconcileWithSnapshot('school_tags', schoolTagList, mapSchoolTag);
  reconcileWithSnapshot('languages', languageList);
  if (expectedPositionList.length) {
    const expectedRow = findRowByTitle('期望职位');
    const expectedRowText = normalized(textOf(expectedRow));
    const expectedTargetsForCheck = asList(result.verify.expectedPositionFinalTargets).length
      ? asList(result.verify.expectedPositionFinalTargets)
      : expectedPositionList;
    const expectedPositionMatched = expectedTargetsForCheck.some(value => expectedRowText.includes(normalized(value)));
    if (!expectedPositionMatched) {
      delete result.applied.expected_position;
      result.skipped.expected_position = `最终未命中（目标：${expectedTargetsForCheck.join('、')}）`;
    }
  }

  const groups = {
    '基础检索': ['keyword_match', 'keywords', 'position_keywords', 'company_keywords'],
    '硬性条件': ['current_city', 'expected_city', 'work_years', 'education', 'age_min', 'age_max', 'recruit_type', 'school_tags'],
    '画像偏好': ['current_industry', 'current_position', 'active_days', 'gender', 'job_hopping', 'languages'],
    '扩展筛选': ['expected_industry', 'expected_position', 'schools', 'majors', 'job_status', 'resume_language'],
  };
  result.groupResults = {};
  for (const [groupName, keys] of Object.entries(groups)) {
    const appliedKeys = keys.filter(key => Object.prototype.hasOwnProperty.call(result.applied, key));
    const skippedKeys = keys.filter(key => Object.prototype.hasOwnProperty.call(result.skipped, key));
    result.groupResults[groupName] = {
      applied: appliedKeys,
      skipped: skippedKeys,
      total: keys.length,
    };
  }

  const searchButton = allVisibleButtons().find(el => /搜\\s*索|查\\s*询|找\\s*人/.test(textOf(el)));
  result.searchButtonFound = Boolean(searchButton);
  if (searchButton && shouldClickSearch) {
    clickLikeUser(searchButton);
    result.clickedSearch = true;
  }

  const keywordInput = documents.map(doc => doc.querySelector('#rc_select_1')).find(Boolean);
  result.keywordInputValue = keywordInput ? (keywordInput.value || '') : '';
  const positionInput = documents.map(doc => doc.querySelector('#rc_select_2')).find(Boolean);
  const companyInput = documents.map(doc => doc.querySelector('#rc_select_4')).find(Boolean);
  const currentPositionInput = (() => {
    const row = findRowByTitle('当前职位');
    return row ? row.querySelector('input.search-component-input, input.ant-input.search-component-input') : null;
  })();
  result.verify = {
    ...(result.verify || {}),
    stepDelayMs,
    stepTrace: stepTrace.slice(0, 160),
    keywordInputValue: result.keywordInputValue,
    positionInputValue: positionInput ? (positionInput.value || '') : '',
    companyInputValue: companyInput ? (companyInput.value || '') : '',
    currentPositionInputValue: currentPositionInput ? (currentPositionInput.value || '') : '',
    keywordMatch: payload.keyword_match || '',
    recruitType: payload.recruit_type || '',
    rowTagSnapshots: finalTagSnapshots,
    rowInspectors: {
      current_city: collectRowInspector('目前城市', '目前城市'),
      expected_city: collectRowInspector('期望城市', '期望城市'),
      age: collectRowInspector('年龄', '年龄'),
      school_tags: collectRowInspector('院校要求', '院校要求'),
      languages: collectRowInspector('语言', '语言'),
      expected_position: collectRowInspector('期望职位', '期望职位'),
    },
  };
  result.url = location.href;
  result.title = document.title;
  resolve(result);
  } catch (error) {
    resolve({
      url: location.href,
      title: document.title,
      documentCount: documents.length,
      applied: {},
      skipped: {},
      searchButtonFound: false,
      clickedSearch: false,
      verify: {},
      error: String((error && error.message) || error || 'unknown error'),
      stack: error && error.stack ? String(error.stack).slice(0, 1200) : '',
    });
  }
}))();
"""


ROUTE_FOCUS_SEARCH_BOX_JS = """
(() => {
  const documents = [];
  const collectDocuments = doc => {
    if (!doc || documents.includes(doc)) return;
    documents.push(doc);
    for (const frame of [...doc.querySelectorAll('iframe')]) {
      try {
        if (frame.contentDocument) collectDocuments(frame.contentDocument);
      } catch (_) {}
    }
  };
  collectDocuments(document);
  const visible = el => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const candidates = documents.flatMap(doc => [...doc.querySelectorAll('*')])
    .filter(el => {
      const text = (el.innerText || el.textContent || '').trim();
      return text.includes('搜职位/公司/行业') || text.includes('中文用空格隔开');
    })
    .sort((a, b) => a.getBoundingClientRect().height - b.getBoundingClientRect().height);
  const textNode = candidates[0];
  const box = textNode ? (textNode.closest('.ant-select, [class*="select"], [role="combobox"]') || textNode) : null;
  const inputs = documents.flatMap(doc => [...doc.querySelectorAll('input')]);
  const input = documents.map(doc => doc.querySelector('#rc_select_1')).find(Boolean)
    || inputs.find(el => /rc_select_1/.test([el.id, el.name, el.className].filter(Boolean).join(' ')))
    || inputs.find(el => /职位|公司|行业|搜索/.test(el.placeholder || ''));
  const target = input || box;
  if (target) {
    target.scrollIntoView({ block: 'center' });
    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
    target.click();
    if (target.focus) target.focus();
  }
  return {
    focused: Boolean(target),
    usedInput: Boolean(input),
    activeTag: document.activeElement ? document.activeElement.tagName : '',
    activeId: document.activeElement ? document.activeElement.id : '',
    activeRole: document.activeElement ? document.activeElement.getAttribute('role') : '',
    documentCount: documents.length,
    inputCount: inputs.length,
    url: location.href,
    title: document.title
  };
})();
"""


ROUTE_CLICK_SEARCH_BUTTON_JS = """
(() => {
  const recordedHints = %s;
  const documents = [];
  const collectDocuments = doc => {
    if (!doc || documents.includes(doc)) return;
    documents.push(doc);
    for (const frame of [...doc.querySelectorAll('iframe')]) {
      try {
        if (frame.contentDocument) collectDocuments(frame.contentDocument);
      } catch (_) {}
    }
  };
  collectDocuments(document);
  const visible = el => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const controls = documents.flatMap(doc => [...doc.querySelectorAll('button, a, [role="button"], .ant-btn, [class*="search"], [class*="Search"]')])
    .filter(visible);
  const normalized = text => String(text || '').replace(/\\s+/g, '').trim().toLowerCase();
  const textOf = el => String((el && (el.innerText || el.textContent)) || '').replace(/\\s+/g, ' ').trim();
  const clickLikeUser = el => {
    if (!el) return;
    try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (_) {}
    const rect = el.getBoundingClientRect();
    const x = Math.round(rect.left + rect.width / 2);
    const y = Math.round(rect.top + rect.height / 2);
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
      try {
        el.dispatchEvent(new MouseEvent(type, {
          bubbles: true,
          cancelable: true,
          view: window,
          button: 0,
          clientX: x,
          clientY: y,
        }));
      } catch (_) {}
    }
  };
  const classTokens = cls => String(cls || '')
    .split(/\\s+/)
    .map(item => item.trim().toLowerCase())
    .filter(item => item.length >= 3);
  const scoreByHint = (el, hint) => {
    if (!el || !hint || typeof hint !== 'object') return -1;
    const tag = String(el.tagName || '').toLowerCase();
    const elText = normalized(el.innerText || el.textContent || '');
    const elId = String(el.id || '').trim();
    const elRole = String(el.getAttribute ? (el.getAttribute('role') || '') : '').trim().toLowerCase();
    const hintText = normalized(hint.text || '');
    const hintTag = String(hint.tag || '').trim().toLowerCase();
    const hintId = String(hint.id || '').trim();
    const hintRole = String(hint.role || '').trim().toLowerCase();
    const hintClsTokens = classTokens(hint.cls || '');
    const elClsTokens = classTokens(el.className || '');
    let score = 0;
    if (hintId && elId && hintId === elId) score += 180;
    if (hintTag && tag === hintTag) score += 40;
    if (hintRole && elRole && hintRole === elRole) score += 25;
    if (hintText && elText) {
      if (hintText === elText) score += 120;
      else if (elText.includes(hintText) || hintText.includes(elText)) score += 80;
    }
    if (hintClsTokens.length && elClsTokens.length) {
      let overlap = 0;
      for (const token of hintClsTokens) {
        if (elClsTokens.includes(token)) overlap += 1;
      }
      score += overlap * 18;
    }
    return score;
  };
  const input = documents.map(doc => doc.querySelector('#rc_select_1')).find(Boolean)
    || documents.flatMap(doc => [...doc.querySelectorAll('input')]).find(el => /职位|公司|行业|搜索/.test(el.placeholder || ''));
  const scoreSearchButton = el => {
    const text = textOf(el);
    const compactText = normalized(text);
    const cls = String(el.className || '');
    const id = String(el.id || '');
    const role = String(el.getAttribute ? (el.getAttribute('role') || '') : '');
    const combined = `${text} ${cls} ${id} ${role}`;
    if (/保存条件|批量查看|立即沟通|继续沟通|查看|收藏|转发|重置|清空|新增|编辑|删除/.test(text)) return -100;
    let score = 0;
    if (compactText === '搜索') score += 150;
    else if (/^搜索$|搜\\s*索/.test(text)) score += 130;
    else if (/查询|找人/.test(text)) score += 60;
    else if (/搜索/.test(text)) score += 40;
    if (/search/i.test(`${cls} ${id}`)) score += 45;
    if (/ant-btn-primary|primary|btn-primary/i.test(cls)) score += 20;
    if (/button/i.test(el.tagName || '') || role === 'button' || /ant-btn/i.test(cls)) score += 15;
    if (text.length > 8 && !/搜索|查询|找人/.test(text)) score -= 70;
    if (input && input.getBoundingClientRect && el.getBoundingClientRect) {
      const inputRect = input.getBoundingClientRect();
      const rect = el.getBoundingClientRect();
      const sameRow = rect.top < inputRect.bottom + 45 && rect.bottom > inputRect.top - 45;
      const rightSide = rect.left >= inputRect.left + inputRect.width * 0.35;
      const closeRight = rect.left <= inputRect.right + 260;
      if (sameRow) score += 40;
      if (sameRow && rightSide && closeRight) score += 80;
    }
    return score;
  };
  const directCandidates = controls
    .map(el => ({ el, score: scoreSearchButton(el) }))
    .filter(item => item.score >= 100)
    .sort((a, b) => b.score - a.score);
  const hintList = Array.isArray(recordedHints) ? recordedHints.filter(item => item && typeof item === 'object') : [];
  let hintBest = null;
  if (hintList.length) {
    for (const btn of controls) {
      for (const hint of hintList) {
        const score = scoreByHint(btn, hint);
        if (!hintBest || score > hintBest.score) hintBest = { btn, score };
      }
    }
  }
  let searchButton = directCandidates[0] ? directCandidates[0].el : null;
  let matchedByHint = false;
  let resultSource = 'direct_dom_search_button_v1';
  if (!searchButton && hintBest && hintBest.score >= 70) {
    searchButton = hintBest.btn;
    matchedByHint = true;
    resultSource = 'recorded_hint_fallback';
  }
  const result = {
    resultSource,
    clickedSearch: Boolean(searchButton),
    matchedByHint,
    submittedByEnter: false,
    hintCount: hintList.length,
    directCandidateCount: directCandidates.length,
    bestDirectScore: directCandidates[0] ? directCandidates[0].score : 0,
    bestHintScore: hintBest ? hintBest.score : 0,
    searchButtonClass: searchButton ? String(searchButton.className || '') : '',
    searchButtonText: searchButton ? ((searchButton.innerText || searchButton.textContent || '').trim()) : '',
    inputValue: (input || document.activeElement || {}).value || '',
    activeTag: document.activeElement ? document.activeElement.tagName : '',
    activeId: document.activeElement ? document.activeElement.id : '',
    documentCount: documents.length,
    url: location.href,
    title: document.title
  };
  try {
    window.__liepin_last_search_click_result__ = JSON.stringify(result);
  } catch (_) {}
  if (searchButton) {
    window.setTimeout(() => {
      clickLikeUser(searchButton);
    }, 0);
  }
  return JSON.stringify(result);
})();
"""


COLLECT_CARDS_JS = """
(() => {
  try {
  const documents = [];
  const collectDocuments = doc => {
    if (!doc || documents.includes(doc)) return;
    documents.push(doc);
    for (const frame of [...doc.querySelectorAll('iframe')]) {
      try {
        if (frame.contentDocument) collectDocuments(frame.contentDocument);
      } catch (_) {}
    }
  };
  collectDocuments(document);
  const visible = el => {
    if (!el || !el.getBoundingClientRect) return false;
    try {
      const rect = el.getBoundingClientRect();
      const view = (el.ownerDocument && el.ownerDocument.defaultView) || window;
      const style = view.getComputedStyle ? view.getComputedStyle(el) : window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
    } catch (_) {
      return false;
    }
  };
  const normalize = text => String(text || '').replace(/\\s+/g, ' ').trim();
  const isFilterConditionText = text => {
    const value = normalize(text);
    if (!value) return false;
    if (/求职期望/.test(value)) return false;
    return /保存条件|包含任意关键词|包含全部关键词|职位名称：|正在发布的|快捷搜索|目前城市：|期望城市：|工作年限：|教育经历：|院校要求：|当前行业：|当前职位：|跳槽频率|简历语言/.test(value);
  };
  const candidateTextLooksReal = text => {
    const value = normalize(text);
    if (!value || isFilterConditionText(value)) return false;
    const hasName = /(?:^|\\s|在线|活跃)([\\u4e00-\\u9fa5A-Za-z][\\*＊]{1,3})(?:\\s|$|名片简历)/.test(value);
    const hasAge = /(?:^|\\D)(?:1[89]|[2-5]\\d|6[0-5])岁/.test(value);
    const hasExpectation = /求职期望/.test(value);
    const hasCareer = /工作\\d+年|\\d{4}年毕业|本科|硕士|博士|大专|中专|初中及以下|高中/.test(value);
    const hasAction = /立即沟通|继续沟通|聊一聊|查看联系方式/.test(value);
    return (hasExpectation && hasAge) || (hasName && hasAge && hasCareer) || (hasName && hasAction && (hasAge || hasExpectation));
  };
  const cardSelector = [
    '.detail-resume-card-wrap',
    '.tlog-common-resume-card',
    '[class*="resume-card"]',
    '[class*="candidate-card"]',
    '[data-candidate-id]',
    '.ant-table-row',
  ].join(',');
  const selectorNodes = documents.flatMap(doc => [...doc.querySelectorAll(cardSelector)]).filter(visible);
  const heuristicNodes = documents
    .flatMap(doc => [...doc.querySelectorAll('div, li, tr, article, section')])
    .filter(visible)
    .filter(el => {
      const text = normalize(el.innerText || el.textContent || '');
      if (!text || text.length < 40 || text.length > 1400) return false;
      if (!candidateTextLooksReal(text)) return false;
      const rect = el.getBoundingClientRect();
      if (rect.height < 80 || rect.height > 680) return false;
      if (el.closest('.search-item, .ant-modal, .ant-select-dropdown, .search-bar, [class*="filter"]')) return false;
      return true;
    });
  const nodes = [...selectorNodes, ...heuristicNodes];
  const cards = [];
  const used = new Set();
  const seenElements = new Set();
  for (const el of nodes) {
    if (seenElements.has(el)) continue;
    seenElements.add(el);
    const text = normalize(el.innerText || el.textContent || '');
    if (!text || text.length < 24) continue;
    const hasGreet = /立即沟通|打招呼|沟通|聊一聊/.test(text);
    const hasProfileSignals = candidateTextLooksReal(text);
    if (!hasGreet && !hasProfileSignals) continue;
    if (!hasProfileSignals) continue;
    const anchors = [...el.querySelectorAll('a')].filter(a => visible(a));
    const href = anchors
      .map(a => a.href || '')
      .find(link => /\/resume\/showresumedetail\/?|res_id_encode=/i.test(link))
      || '';
    const nameNode = el.querySelector('.name, [class*="name"], .candidate-name, .resume-card-name, .user-name');
    const name = normalize(nameNode ? nameNode.innerText || nameNode.textContent || '' : '');
    const rect = el.getBoundingClientRect();
    const key = href || `${name}:${text.slice(0, 80)}`;
    if (used.has(key)) continue;
    used.add(key);
    cards.push({
      name: name || '',
      href,
      text,
      textLength: text.length,
      hasGreet,
      looksLikeResume: hasProfileSignals,
      top: rect.top,
      x: rect.left + rect.width / 2,
      y: rect.top + Math.min(rect.height / 2, 96),
    });
  }
  if (!cards.length) {
    const links = documents
      .flatMap(doc => [...doc.querySelectorAll('a[href]')])
      .filter(visible)
      .map(a => ({ a, href: a.href || '', text: normalize(a.innerText || a.textContent || '') }))
      .filter(item => /\/resume\/showresumedetail\/?|res_id_encode=/i.test(item.href))
      .sort((a, b) => a.a.getBoundingClientRect().top - b.a.getBoundingClientRect().top);
    for (const item of links) {
      const key = item.href;
      if (!key || used.has(key)) continue;
      used.add(key);
      cards.push({
        name: '',
        href: item.href,
        text: item.text || item.href,
        textLength: (item.text || item.href).length,
        hasGreet: false,
        looksLikeResume: true,
        top: item.a.getBoundingClientRect().top,
        x: item.a.getBoundingClientRect().left,
        y: item.a.getBoundingClientRect().top,
      });
      if (cards.length >= 80) break;
    }
  }
  cards.sort((a, b) => a.top - b.top);
  const signatureFor = card => {
    const text = normalize(card.text || '');
    const nameFromText = ((text.match(/(?:今天活跃|最近活跃|30天内活跃)?\\s*([\\u4e00-\\u9fa5A-Za-z][\\*＊]{1,3})/) || [])[1] || '').replace(/＊/g, '*');
    const name = normalize(card.name || nameFromText).replace(/＊/g, '*');
    const age = (text.match(/\\d{2}岁/) || [''])[0];
    const work = (text.match(/工作\\d+年/) || [''])[0];
    const edu = (text.match(/本科|硕士|博士|大专|中专/) || [''])[0];
    const expectation = ((text.match(/求职期望[:：]\\s*([^\\n\\r]{0,40})/) || [])[1] || '').slice(0, 18);
    return [name, age, work, edu, expectation].filter(Boolean).join('|');
  };
  const dedupedCards = [];
  for (const card of [...cards].sort((a, b) => {
    if (Math.abs(a.top - b.top) < 60) return (b.textLength || 0) - (a.textLength || 0);
    return a.top - b.top;
  })) {
    const signature = signatureFor(card);
    const duplicateIndex = dedupedCards.findIndex(existing => {
      const existingSignature = existing.__signature || '';
      if (signature && existingSignature && signature === existingSignature) return true;
      if (Math.abs((existing.top || 0) - (card.top || 0)) < 90) {
        const existingText = normalize(existing.text || '');
        const cardText = normalize(card.text || '');
        if (existingText && cardText && (existingText.includes(cardText) || cardText.includes(existingText))) return true;
      }
      return false;
    });
    if (duplicateIndex >= 0) {
      if ((card.textLength || 0) > (dedupedCards[duplicateIndex].textLength || 0)) {
        dedupedCards[duplicateIndex] = { ...card, __signature: signature };
      }
      continue;
    }
    dedupedCards.push({ ...card, __signature: signature });
  }
  dedupedCards.sort((a, b) => a.top - b.top);
  for (const card of dedupedCards) delete card.__signature;
  const candidateLinks = documents
    .flatMap(doc => [...doc.querySelectorAll('a[href]')])
    .filter(visible)
    .map(a => a.href || '')
    .filter(href => /\/resume\/showresumedetail\/?|res_id_encode=/i.test(href));
  const documentTexts = documents
    .map(doc => normalize((doc.body && (doc.body.innerText || doc.body.textContent)) || ''))
    .filter(Boolean);
  const resultCountText = (
    documentTexts
      .map(text => (text.match(/共\\s*[0-9,+]+\\s*位人选/) || text.match(/[0-9,+]+\\s*位人选/) || [''])[0])
      .find(Boolean)
    || ''
  );
  const sampleRows = heuristicNodes
    .slice(0, 6)
    .map(el => normalize(el.innerText || el.textContent || '').slice(0, 180))
    .filter(Boolean);
  return JSON.stringify({
    url: location.href,
    title: document.title,
    documentCount: documents.length,
    rawCount: nodes.length,
    selectorNodeCount: selectorNodes.length,
    heuristicNodeCount: heuristicNodes.length,
    resultCountText,
    candidateLinkCount: candidateLinks.length,
    sampleCandidateLinks: candidateLinks.slice(0, 10),
    sampleRows,
    count: dedupedCards.length,
    preDedupCount: cards.length,
    cards: dedupedCards.slice(0, 80),
  });
  } catch (error) {
    return JSON.stringify({
      url: location.href,
      title: document.title,
      error: String((error && error.message) || error || 'collect_cards_error'),
      stack: error && error.stack ? String(error.stack).slice(0, 1200) : '',
      count: 0,
      cards: [],
    });
  }
})();
"""


CLICK_NEXT_PAGE_JS = """
(() => {
  try {
    const documents = [];
    const collectDocuments = doc => {
      if (!doc || documents.includes(doc)) return;
      documents.push(doc);
      for (const frame of [...doc.querySelectorAll('iframe')]) {
        try {
          if (frame.contentDocument) collectDocuments(frame.contentDocument);
        } catch (_) {}
      }
    };
    collectDocuments(document);
    const normalize = text => String(text || '').replace(/\\s+/g, ' ').trim();
    const visible = el => {
      if (!el || !el.getBoundingClientRect) return false;
      try {
        const rect = el.getBoundingClientRect();
        const view = (el.ownerDocument && el.ownerDocument.defaultView) || window;
        const style = view.getComputedStyle ? view.getComputedStyle(el) : window.getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
      } catch (_) {
        return false;
      }
    };
    const isDisabled = el => {
      if (!el) return true;
      try {
        if (el.disabled || el.getAttribute('aria-disabled') === 'true') return true;
        const cls = String(el.className || '');
        return /disabled|is-disabled|ant-pagination-disabled|pager-disabled/i.test(cls);
      } catch (_) {
        return false;
      }
    };
    const clickLikeUser = el => {
      if (!el) return;
      el.scrollIntoView({ block: 'center', inline: 'center' });
      const rect = el.getBoundingClientRect();
      const x = Math.round(rect.left + rect.width / 2);
      const y = Math.round(rect.top + rect.height / 2);
      for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
        try {
          el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window, button: 0, clientX: x, clientY: y }));
        } catch (_) {}
      }
    };
    const findInPagination = () => {
      for (const doc of documents) {
        const nextItems = [...doc.querySelectorAll('.ant-pagination-next, li[title="下一页"], li[aria-label*="next"], li[aria-label*="Next"], li[aria-label*="下一页"]')]
          .filter(visible)
          .filter(el => !isDisabled(el));
        for (const item of nextItems) {
          const target = [...item.querySelectorAll('button, a, span')].find(node => visible(node) && !isDisabled(node)) || item;
          if (target && !isDisabled(target)) return { item, target, source: 'ant-pagination-next' };
        }
      }
      return null;
    };
    const nodes = documents.flatMap(doc => [...doc.querySelectorAll('a, button, li, span, div')]).filter(visible);
    const fallbackNode = nodes.find(el => {
      const text = normalize(el.innerText || el.textContent || '');
      const title = normalize(el.getAttribute('title') || '');
      const aria = normalize(el.getAttribute('aria-label') || '');
      const cls = normalize(el.className || '');
      const combined = `${text} ${title} ${aria} ${cls}`;
      if (!/下一页|下页|next/i.test(combined)) return false;
      if (isDisabled(el)) return false;
      const parent = el.closest('a, button, li, [role="button"]') || el;
      if (isDisabled(parent)) return false;
      return true;
    });
    const paginationTarget = findInPagination();
    const target = paginationTarget
      ? paginationTarget.target
      : (fallbackNode ? (fallbackNode.closest('a, button, li, [role="button"]') || fallbackNode) : null);
    const targetRoot = paginationTarget ? paginationTarget.item : target;
    const beforeUrl = location.href;
    const beforeTitle = document.title;
    if (target) {
      clickLikeUser(target);
      return JSON.stringify({
        foundNext: true,
        clickedNext: true,
        source: paginationTarget ? paginationTarget.source : 'fallback-text',
        buttonText: normalize(targetRoot.innerText || targetRoot.textContent || ''),
        buttonClass: String(targetRoot.className || ''),
        targetTag: String(target.tagName || '').toLowerCase(),
        targetClass: String(target.className || ''),
        urlBefore: beforeUrl,
        urlAfter: location.href,
        titleBefore: beforeTitle,
        titleAfter: document.title,
        documentCount: documents.length
      });
    }
    return JSON.stringify({
      foundNext: false,
      clickedNext: false,
      urlBefore: beforeUrl,
      urlAfter: location.href,
      titleBefore: beforeTitle,
      titleAfter: document.title,
      documentCount: documents.length
    });
  } catch (error) {
    return JSON.stringify({
      foundNext: false,
      clickedNext: false,
      error: String((error && error.message) || error || 'next_page_error'),
      stack: error && error.stack ? String(error.stack).slice(0, 1200) : '',
      url: location.href,
      title: document.title
    });
  }
})();
"""


TOGGLE_RESULT_FILTER_JS = """
(() => {
  try {
    const targetText = %r;
    const documents = [];
    const collectDocuments = doc => {
      if (!doc || documents.includes(doc)) return;
      documents.push(doc);
      for (const frame of [...doc.querySelectorAll('iframe')]) {
        try {
          if (frame.contentDocument) collectDocuments(frame.contentDocument);
        } catch (_) {}
      }
    };
    collectDocuments(document);
    const normalize = text => String(text || '').replace(/\\s+/g, ' ').trim();
    const visible = el => {
      if (!el || !el.getBoundingClientRect) return false;
      try {
        const rect = el.getBoundingClientRect();
        const view = (el.ownerDocument && el.ownerDocument.defaultView) || window;
        const style = view.getComputedStyle ? view.getComputedStyle(el) : window.getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
      } catch (_) {
        return false;
      }
    };
    const clickLikeUser = el => {
      if (!el) return;
      el.scrollIntoView({ block: 'center', inline: 'center' });
      const rect = el.getBoundingClientRect();
      const x = Math.round(rect.left + rect.width / 2);
      const y = Math.round(rect.top + rect.height / 2);
      for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
        try {
          el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window, button: 0, clientX: x, clientY: y }));
        } catch (_) {}
      }
    };
    const checkedState = wrapper => {
      const input = wrapper ? wrapper.querySelector('input[type="checkbox"]') : null;
      if (input) return Boolean(input.checked);
      const ariaNode = wrapper && (wrapper.matches('[aria-checked]') ? wrapper : wrapper.querySelector('[aria-checked]'));
      if (ariaNode) return ariaNode.getAttribute('aria-checked') === 'true';
      const checkboxNode = wrapper ? wrapper.querySelector('[class*="checkbox"]') : null;
      const cls = `${String(wrapper && wrapper.className || '')} ${String(checkboxNode && checkboxNode.className || '')}`;
      return /checked|is-checked|ant-checkbox-wrapper-checked|ant-checkbox-checked/i.test(cls);
    };
    const info = el => {
      if (!el) return {};
      const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
      return {
        tag: String(el.tagName || '').toLowerCase(),
        cls: String(el.className || ''),
        text: normalize(el.innerText || el.textContent || '').slice(0, 120),
        rect: rect ? {
          left: Math.round(rect.left),
          top: Math.round(rect.top),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        } : {},
      };
    };
    const candidates = [];
    const addCandidate = wrapper => {
      if (!wrapper || !visible(wrapper)) return;
      const input = wrapper.matches('input[type="checkbox"]') ? wrapper : wrapper.querySelector('input[type="checkbox"]');
      if (!input) return;
      const text = normalize(wrapper.innerText || wrapper.textContent || input.getAttribute('aria-label') || '');
      if (!text.includes(targetText)) return;
      if (text.length > Math.max(80, targetText.length + 40)) return;
      if (candidates.some(item => item.wrapper === wrapper || item.input === input)) return;
      const rect = wrapper.getBoundingClientRect ? wrapper.getBoundingClientRect() : null;
      candidates.push({
        wrapper,
        input,
        text,
        checked: checkedState(wrapper),
        area: rect ? Math.round(rect.width * rect.height) : 999999,
      });
    };
    for (const doc of documents) {
      for (const input of [...doc.querySelectorAll('input[type="checkbox"]')].filter(visible)) {
        addCandidate(input.closest('label, .ant-checkbox-wrapper, [role="checkbox"]') || input.parentElement || input);
      }
      for (const wrapper of [...doc.querySelectorAll('label, .ant-checkbox-wrapper, [role="checkbox"]')].filter(visible)) {
        addCandidate(wrapper);
      }
    }
    candidates.sort((a, b) => a.text.length - b.text.length || a.area - b.area);
    const target = candidates[0] || null;
    const before = target ? checkedState(target.wrapper) : false;
    let clicked = false;
    if (target && !before) {
      clickLikeUser(target.wrapper || target.input);
      clicked = true;
    }
    const after = target ? checkedState(target.wrapper) : false;
    return JSON.stringify({
      ok: Boolean(target && after),
      targetText,
      found: Boolean(target),
      clicked,
      before,
      after,
      candidateCount: candidates.length,
      candidates: candidates.slice(0, 8).map(item => ({
        text: item.text,
        checked: item.checked,
        area: item.area,
        wrapper: info(item.wrapper),
        input: info(item.input),
      })),
      url: location.href,
    });
  } catch (error) {
    return JSON.stringify({
      ok: false,
      targetText: %r,
      error: String((error && error.message) || error || 'unknown'),
      stack: error && error.stack ? String(error.stack).slice(0, 1000) : '',
      url: location.href,
    });
  }
})();
"""


OPEN_CANDIDATE_BY_INDEX_JS = """
(() => {
  try {
  const requestedIndex = Math.max(0, Number(%s) || 0);
  const documents = [];
  const collectDocuments = doc => {
    if (!doc || documents.includes(doc)) return;
    documents.push(doc);
    for (const frame of [...doc.querySelectorAll('iframe')]) {
      try {
        if (frame.contentDocument) collectDocuments(frame.contentDocument);
      } catch (_) {}
    }
  };
  collectDocuments(document);
  const visible = el => {
    if (!el || !el.getBoundingClientRect) return false;
    try {
      const rect = el.getBoundingClientRect();
      const view = (el.ownerDocument && el.ownerDocument.defaultView) || window;
      const style = view.getComputedStyle ? view.getComputedStyle(el) : window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
    } catch (_) {
      return false;
    }
  };
  const normalize = text => String(text || '').replace(/\\s+/g, ' ').trim();
  const clickLikeUser = el => {
    if (!el) return;
    el.scrollIntoView({ block: 'center', inline: 'center' });
    // 只触发一次真实点击，避免同一个候选人被连点打开两个详情页。
    if (typeof el.click === 'function') el.click();
    else el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window, button: 0 }));
  };
  const targetInfo = el => {
    if (!el) return {};
    return {
      tag: String(el.tagName || '').toLowerCase(),
      id: String(el.id || ''),
      cls: String(el.className || ''),
      text: normalize(el.innerText || el.textContent || '').slice(0, 80),
    };
  };
  const isFilterConditionText = text => {
    const value = normalize(text);
    if (!value) return false;
    if (/求职期望/.test(value)) return false;
    return /保存条件|包含任意关键词|包含全部关键词|职位名称：|正在发布的|快捷搜索|目前城市：|期望城市：|工作年限：|教育经历：|院校要求：|当前行业：|当前职位：|跳槽频率|简历语言/.test(value);
  };
  const candidateTextLooksReal = text => {
    const value = normalize(text);
    if (!value || isFilterConditionText(value)) return false;
    const hasName = /(?:^|\\s|在线|活跃)([\\u4e00-\\u9fa5A-Za-z][\\*＊]{1,3})(?:\\s|$|名片简历)/.test(value);
    const hasAge = /(?:^|\\D)(?:1[89]|[2-5]\\d|6[0-5])岁/.test(value);
    const hasExpectation = /求职期望/.test(value);
    const hasCareer = /工作\\d+年|\\d{4}年毕业|本科|硕士|博士|大专|中专|初中及以下|高中/.test(value);
    const hasAction = /立即沟通|继续沟通|聊一聊|查看联系方式/.test(value);
    return (hasExpectation && hasAge) || (hasName && hasAge && hasCareer) || (hasName && hasAction && (hasAge || hasExpectation));
  };
  const selectors = [
    '.detail-resume-card-wrap',
    '.tlog-common-resume-card',
    '[class*="resume-card"]',
    '[class*="candidate-card"]',
    '[data-candidate-id]',
    '.ant-table-row',
  ];
  const selectorCards = documents.flatMap(doc => [...doc.querySelectorAll(selectors.join(','))])
    .filter(visible)
    .map((el, index) => {
      const text = normalize(el.innerText || el.textContent || '');
      const rect = el.getBoundingClientRect();
      const anchors = [...el.querySelectorAll('a')].filter(a => visible(a));
      const href = (anchors
        .map(a => a.href || '')
        .find(href => /\/resume\/showresumedetail\/?|res_id_encode=/i.test(href))) || '';
      const link = anchors.find(a => /\/resume\/showresumedetail\/?|res_id_encode=/i.test(a.href || '')) || null;
      const looksLikeResume = candidateTextLooksReal(text);
      return { el, link, index, text, textLength: text.length, href, looksLikeResume, top: rect.top, rect };
    })
    .filter(card => card.textLength > 30 && card.looksLikeResume)
    .sort((a, b) => a.top - b.top);
  const heuristicCards = documents
    .flatMap(doc => [...doc.querySelectorAll('div, li, tr, article, section')])
    .filter(visible)
    .map((el, index) => {
      const text = normalize(el.innerText || el.textContent || '');
      if (!text || text.length < 40 || text.length > 1400) return null;
      if (!candidateTextLooksReal(text)) return null;
      if (el.closest('.search-item, .ant-modal, .ant-select-dropdown, .search-bar, [class*="filter"]')) return null;
      const rect = el.getBoundingClientRect();
      if (rect.height < 80 || rect.height > 680) return null;
      const anchors = [...el.querySelectorAll('a')].filter(a => visible(a));
      const href = anchors
        .map(a => a.href || '')
        .find(link => /\/resume\/showresumedetail\/?|res_id_encode=/i.test(link))
        || '';
      const link = anchors.find(a => /\/resume\/showresumedetail\/?|res_id_encode=/i.test(a.href || ''))
        || anchors.find(a => normalize(a.innerText || a.textContent || '').length <= 40)
        || null;
      return { el, link, index, text, textLength: text.length, href, looksLikeResume: true, top: rect.top, rect };
    })
    .filter(Boolean)
    .sort((a, b) => a.top - b.top);
  const rawCards = [...selectorCards, ...heuristicCards]
    .sort((a, b) => a.top - b.top)
    .filter((item, idx, arr) => {
      if (!item || !item.el) return false;
      return arr.findIndex(v => v && v.el === item.el) === idx;
    });
  const signatureFor = card => {
    const text = normalize(card.text || '');
    const nameFromText = ((text.match(/(?:今天活跃|最近活跃|30天内活跃)?\\s*([\\u4e00-\\u9fa5A-Za-z][\\*＊]{1,3})/) || [])[1] || '').replace(/＊/g, '*');
    const name = normalize(nameFromText).replace(/＊/g, '*');
    const age = (text.match(/\\d{2}岁/) || [''])[0];
    const work = (text.match(/工作\\d+年/) || [''])[0];
    const edu = (text.match(/本科|硕士|博士|大专|中专/) || [''])[0];
    const expectation = ((text.match(/求职期望[:：]\\s*([^\\n\\r]{0,40})/) || [])[1] || '').slice(0, 18);
    return [name, age, work, edu, expectation].filter(Boolean).join('|');
  };
  const cards = [];
  for (const card of [...rawCards].sort((a, b) => {
    if (Math.abs(a.top - b.top) < 60) return (b.textLength || 0) - (a.textLength || 0);
    return a.top - b.top;
  })) {
    const signature = signatureFor(card);
    const duplicateIndex = cards.findIndex(existing => {
      const existingSignature = existing.__signature || '';
      if (signature && existingSignature && signature === existingSignature) return true;
      if (Math.abs((existing.top || 0) - (card.top || 0)) < 90) {
        const existingText = normalize(existing.text || '');
        const cardText = normalize(card.text || '');
        if (existingText && cardText && (existingText.includes(cardText) || cardText.includes(existingText))) return true;
      }
      return false;
    });
    if (duplicateIndex >= 0) {
      if ((card.textLength || 0) > (cards[duplicateIndex].textLength || 0)) {
        cards[duplicateIndex] = { ...card, __signature: signature };
      }
      continue;
    }
    cards.push({ ...card, __signature: signature });
  }
  cards.sort((a, b) => a.top - b.top);
  if (!cards.length) {
    return JSON.stringify({
      clicked: false,
      reason: '未找到候选人卡片/详情链接；请确认已经搜索出结果。',
      documentCount: documents.length,
      selectorCardCount: selectorCards.length,
      heuristicCardCount: heuristicCards.length,
      preDedupCardCount: rawCards.length,
      url: location.href,
      title: document.title
    });
  }
  if (requestedIndex >= cards.length) {
    return JSON.stringify({
      clicked: false,
      reason: 'target_index_out_of_range',
      requestedIndex,
      selectedIndex: null,
      cardCount: cards.length,
      message: `目标候选人序号 ${requestedIndex + 1} 超出当前可点击卡片数 ${cards.length}`,
      documentCount: documents.length,
      selectorCardCount: selectorCards.length,
      heuristicCardCount: heuristicCards.length,
      preDedupCardCount: rawCards.length,
      url: location.href,
      title: document.title
    });
  }
  const selectedIndex = requestedIndex;
  const card = cards[selectedIndex];
  const openTarget = card.link
    || card.el.querySelector('.name, [class*="name"], .candidate-name, .resume-card-name, [class*="card-name"]')
    || [...card.el.querySelectorAll('a, span, div, p')].find(node => {
      const txt = normalize(node.innerText || node.textContent || '');
      if (!txt || txt.length > 24) return false;
      if (/收藏|转发|查看联系方式|批量查看|沟通|打招呼/.test(txt)) return false;
      return true;
    })
    || card.el;
  const urlBefore = location.href;
  if (card.href) {
    return JSON.stringify({
      clicked: true,
      directNavigate: true,
      usedLink: Boolean(card.link),
      href: card.href,
      urlBefore,
      urlAfter: location.href,
      textPreview: card.text.slice(0, 300),
      target: targetInfo(card.link || openTarget),
      cardIndex: card.index,
      requestedIndex,
      selectedIndex,
      cardCount: cards.length,
      cardTop: Math.round(card.rect.top || 0),
      documentCount: documents.length,
      selectorCardCount: selectorCards.length,
      heuristicCardCount: heuristicCards.length,
      preDedupCardCount: rawCards.length,
      url: location.href,
      title: document.title
    });
  }
  let usedLink = false;
  clickLikeUser(openTarget);
  return JSON.stringify({
    clicked: true,
    directNavigate: false,
    usedLink,
    href: card.href || '',
    urlBefore,
    urlAfter: location.href,
    textPreview: card.text.slice(0, 300),
    target: targetInfo(openTarget),
    cardIndex: card.index,
    requestedIndex,
    selectedIndex,
    cardCount: cards.length,
    cardTop: Math.round(card.rect.top || 0),
    documentCount: documents.length,
    selectorCardCount: selectorCards.length,
    heuristicCardCount: heuristicCards.length,
    preDedupCardCount: rawCards.length,
    url: location.href,
    title: document.title
  });
  } catch (error) {
    return JSON.stringify({
      clicked: false,
      reason: String((error && error.message) || error || 'open_first_error'),
      stack: error && error.stack ? String(error.stack).slice(0, 1200) : '',
      url: location.href,
      title: document.title
    });
  }
})();
"""

OPEN_FIRST_CANDIDATE_JS = OPEN_CANDIDATE_BY_INDEX_JS % 0


PREPARE_RESUME_JS = """
(() => {
  const text = document.body ? document.body.innerText || '' : '';
  const expandButtons = [...document.querySelectorAll('.rd-info-other-link')]
    .filter(el => /显示其他\\d+段项目经历/.test((el.innerText || el.textContent || '').trim()));
  const clicked = [];
  for (const el of expandButtons) {
    clicked.push((el.innerText || el.textContent || '').trim());
    el.scrollIntoView({ block: 'center' });
    el.click();
  }
  return {
    url: location.href,
    isResumeDetail: /\\/resume\\/showresumedetail\\/?|res_id_encode=/.test(location.href),
    beforeTextLength: text.length,
    clickedExpanders: clicked
  };
})();
"""


INSPECT_RESUME_JS = """
(() => {
  const normalize = text => String(text || '').replace(/\\s+/g, ' ').trim();
  const visible = el => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const bodyText = document.body ? document.body.innerText || '' : '';
  const sectionWords = [
    '简历信息', '基本信息', '求职意向', '工作经历', '工作经验', '项目经历', '教育经历',
    '资格证书', '语言能力', '我的技能', '技能', '专业技能', '自我评价', '个人优势',
    '附加消息', '期望薪资', '目前状态'
  ];
  const actionWords = ['打招呼', '沟通', '聊一聊', '联系', '查看联系方式'];
  const matchedSections = sectionWords.filter(word => bodyText.includes(word));
  const matchedActions = actionWords.filter(word => bodyText.includes(word));
  const cards = [...document.querySelectorAll('[data-candidate-id], .candidate-card, .resume-card, .user-card, .detail-resume-card-wrap')]
    .filter(visible).length;
  const explicitNameSelectors = [
    '.resume-name',
    '.resume-user-name',
    '.new-resume-personal-name',
    '[class*="resume-name"]',
    '[class*="candidate-name"]',
    '[class*="personal-name"]'
  ];
  const invalidName = text => /每日任务|我的主页|个人中心|安全中心|中文简历|查看大图|立即沟通|继续沟通|求职意向|简历信息/.test(text);
  const nameNode = explicitNameSelectors
    .flatMap(selector => [...document.querySelectorAll(selector)])
    .filter(visible)
    .find(el => {
      const text = normalize(el.innerText || el.textContent || '');
      return text && text.length <= 20 && !invalidName(text);
    });
  const bodyLines = bodyText.split(/\\n+/).map(line => normalize(line)).filter(Boolean);
  const nameFromLines = (() => {
    const start = bodyLines.findIndex(line => line === '查看大图' || /简历编号[:：]/.test(line));
    const slice = bodyLines.slice(Math.max(0, start), Math.min(bodyLines.length, start + 12));
    const candidate = slice.find(line => {
      if (!line || invalidName(line)) return false;
      if (/^(在线|今天活跃|3天内活跃|30天内活跃|在职|离职|男|女|方便联系时间)/.test(line)) return false;
      if (/\\d{2}岁|工作\\d+年|本科|硕士|博士|大专|中专/.test(line)) return false;
      return /^[\\u4e00-\\u9fa5A-Za-z][\\u4e00-\\u9fa5A-Za-z\\*＊先生女士]{1,12}(?:\\s*阅)?$/.test(line);
    });
    return candidate || '';
  })();
  const candidateName = normalize(nameNode ? nameNode.innerText || nameNode.textContent || '' : nameFromLines);
  const projectHeader = bodyText.match(/项目经历（共(\\d+)段）/);
  const projectTotal = projectHeader ? Number(projectHeader[1]) : null;
  const projectVisible = (bodyText.match(/项目职务：/g) || []).length;
  const remainingProjectExpanders = [...document.querySelectorAll('.rd-info-other-link')]
    .map(el => (el.innerText || el.textContent || '').trim())
    .filter(text => /显示其他\\d+段项目经历/.test(text));
  return {
    url: location.href,
    title: document.title,
    isResumeDetail: /\\/resume\\/showresumedetail\\/?|res_id_encode=/.test(location.href),
    textLength: bodyText.length,
    lineCount: bodyText.split(/\\n+/).filter(Boolean).length,
    candidateName,
    matchedSections,
    matchedActions,
    visibleCandidateCards: cards,
    projectTotal,
    projectVisible,
    remainingProjectExpanders,
    hasAttachmentResume: bodyText.includes('附件简历') || bodyText.includes('已上传附件简历'),
    hasUnauthorizedAttachment: bodyText.includes('索要附件') || bodyText.includes('索要简历') || bodyText.includes('向TA索要'),
    hasResumeSignal: matchedSections.length >= 3 || matchedActions.length > 0,
    bodyPreview: bodyText.replace(/\\s+/g, ' ').trim().slice(0, 500),
    resumeText: bodyText
  };
})();
"""


GREET_JS = """
(() => {
  const openingMessage = %r;
  const followupMessage = %r;
  const continuedFollowupMessage = %r;
  const dryRun = %s;
  const visible = el => {
    if (!el || !el.getBoundingClientRect) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const normalize = text => String(text || '').replace(/\\s+/g, ' ').trim();
  const info = el => {
    if (!el) return {};
    return {
      tag: String(el.tagName || '').toLowerCase(),
      id: String(el.id || ''),
      cls: String(el.className || ''),
      text: normalize(el.innerText || el.textContent || '').slice(0, 80),
    };
  };
  const sameText = (a, b) => normalize(a).replace(/\\s+/g, '') === normalize(b).replace(/\\s+/g, '');
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const isDisabled = el => {
    if (!el) return true;
    const cls = String(el.className || '');
    return Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true' || /disabled|ant-btn-disabled/i.test(cls);
  };
  const controls = () => [...document.querySelectorAll('button, a, [role="button"], .ant-btn')]
    .filter(visible);
  const currentChatEditor = () => {
    const editors = [...document.querySelectorAll('textarea, input, [contenteditable="true"]')]
      .filter(visible)
      .filter(el => !el.disabled && el.getAttribute('aria-disabled') !== 'true');
    return editors.find(el => {
      const placeholder = normalize(el.getAttribute('placeholder') || '');
      return /请输入文字|按Enter键发送|消息|沟通|回复/.test(placeholder) || editors.length === 1;
    }) || null;
  };
  const fillEditor = (target, message) => {
    target.focus();
    if (target.getAttribute('contenteditable') === 'true') {
      target.innerText = message;
      target.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: message }));
    } else {
      const proto = target.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      if (setter) setter.call(target, message);
      else target.value = message;
      target.dispatchEvent(new Event('input', { bubbles: true }));
    }
    target.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: message }));
    target.dispatchEvent(new Event('change', { bubbles: true }));
    target.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, cancelable: true, key: 'Process', code: 'Process' }));
  };
  const looksLikeMessageBubble = el => {
    if (!el) return false;
    const cls = String(el.className || '');
    if (/im-ui-message-item|message-item|message-body|message-list|chat-message/i.test(cls)) return true;
    const parent = el.closest('[class*="message-item"], [class*="message-list"], [class*="im-ui-message"], [class*="chat-message"]');
    return Boolean(parent);
  };
  const sendCandidates = editor => {
    const nodes = [...document.querySelectorAll('button, a, [role="button"], .ant-btn, [class*="send"], [class*="Send"]')]
      .filter(visible)
      .filter(el => !isDisabled(el))
      .filter(el => !looksLikeMessageBubble(el));
    const editorRect = editor && editor.getBoundingClientRect ? editor.getBoundingClientRect() : null;
    return nodes
      .map(el => {
        const text = normalize(el.innerText || el.textContent || '');
        const title = normalize(el.getAttribute('title') || '');
        const aria = normalize(el.getAttribute('aria-label') || '');
        const cls = normalize(el.className || '');
        const combined = `${text} ${title} ${aria} ${cls}`;
        const rect = el.getBoundingClientRect();
        let score = 0;
        if (/发送|确认发送|立即发送|send/i.test(`${text} ${title} ${aria}`)) score += 12;
        if (/(^|\\s)(send|btn-send|send-btn|im-send)(\\s|$|-|_)/i.test(cls)) score += 8;
        if (/ant-btn-primary|primary/i.test(cls)) score += 2;
        if (editorRect) {
          const nearVertically = rect.top >= editorRect.top - 80 && rect.top <= editorRect.bottom + 160;
          const nearHorizontally = rect.left >= editorRect.left - 120 && rect.left <= editorRect.right + 260;
          if (nearVertically && nearHorizontally) score += 8;
          if (rect.top >= editorRect.top - 20) score += 2;
        }
        if (/取消|关闭|暂不|稍后|跳转|设置|索要|查看|收藏|转发|表情|图片|附件|电话|微信|超约/i.test(combined)) score -= 20;
        if (text.length > 20 && !/发送|send/i.test(`${text} ${title} ${aria}`)) score -= 20;
        return { el, score, text, cls };
      })
      .filter(item => item.score > 0)
      .sort((a, b) => b.score - a.score);
  };
  const sendButton = editor => sendCandidates(editor)[0]?.el || null;
  const visibleSendCandidateInfo = editor => sendCandidates(editor).slice(0, 12).map(item => ({
    ...info(item.el),
    score: item.score,
  }));
  const clickableSendButton = editor => sendButton(editor) || controls().find(el => {
    const text = normalize(el.innerText || el.textContent || '');
    const title = normalize(el.getAttribute('title') || '');
    const aria = normalize(el.getAttribute('aria-label') || '');
    const cls = normalize(el.className || '');
    const combined = `${text} ${title} ${aria} ${cls}`;
    if (looksLikeMessageBubble(el)) return false;
    if (/取消|关闭|暂不|稍后/.test(text)) return false;
    if (isDisabled(el)) return false;
    return /发送|确认发送|立即发送|确定|send/i.test(combined);
  }) || null;
  const clickLikeUser = el => {
    if (!el) return;
    el.scrollIntoView({ block: 'center', inline: 'center' });
    const rect = el.getBoundingClientRect();
    const x = Math.round(rect.left + rect.width / 2);
    const y = Math.round(rect.top + rect.height / 2);
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
      try {
        el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window, button: 0, clientX: x, clientY: y }));
      } catch (_) {}
    }
  };
  const pressEnterToSend = editor => {
    editor.focus();
    for (const type of ['keydown', 'keypress', 'keyup']) {
      try {
        editor.dispatchEvent(new KeyboardEvent(type, {
          bubbles: true,
          cancelable: true,
          key: 'Enter',
          code: 'Enter',
          keyCode: 13,
          which: 13,
        }));
      } catch (_) {}
    }
  };
  const editorText = editor => normalize(editor ? (editor.value || editor.innerText || editor.textContent || '') : '');
  const isEditorClearedAfterSend = (editor, message) => {
    const value = editorText(editor);
    return !value || !value.includes(normalize(message).slice(0, 20));
  };
  const currentConversationScope = editor => {
    if (!editor) return null;
    let node = editor.parentElement;
    let best = null;
    for (let i = 0; node && i < 10; i += 1, node = node.parentElement) {
      if (!visible(node) || !node.contains(editor)) continue;
      const rect = node.getBoundingClientRect();
      const text = normalize(node.innerText || node.textContent || '');
      if (rect.width < 260 || rect.height < 220) continue;
      if (/请输入文字|按Enter键发送|24小时内有回复|沟通职位|索要微信|索要手机|索要简历/.test(text)) {
        best = node;
        break;
      }
    }
    return best || editor.closest('.ant-modal, [role="dialog"], [class*="chat"], [class*="im"]') || null;
  };
  const messageExistsInChat = (message, editor) => {
    const needle = normalize(message).slice(0, 28);
    if (!needle) return false;
    const scope = currentConversationScope(editor);
    if (!scope) return false;
    const editorRect = editor && editor.getBoundingClientRect ? editor.getBoundingClientRect() : null;
    const scopeRect = scope.getBoundingClientRect ? scope.getBoundingClientRect() : null;
    const nodes = [...scope.querySelectorAll(
      '[class*="message-item"], [class*="message-body"], [class*="im-ui-message"], [class*="chat-message"], [class*="bubble"]'
    )]
      .filter(visible)
      .filter(node => {
        if (!editorRect) return true;
        const rect = node.getBoundingClientRect();
        const aboveEditor = rect.bottom <= editorRect.top + 24;
        const horizontalOverlap = rect.right >= editorRect.left - 30 && rect.left <= editorRect.right + 30;
        return aboveEditor && horizontalOverlap;
      });
    const text = normalize((nodes.length ? nodes : [scope]).map(node => node.innerText || node.textContent || '').join(' '));
    const exists = text.includes(needle);
    try {
      window.__liepin_last_followup_duplicate_check__ = JSON.stringify({
        exists,
        needle,
        scoped: Boolean(scope),
        nodeCount: nodes.length,
        scope: info(scope),
        scopeRect: scopeRect ? {
          width: Math.round(scopeRect.width),
          height: Math.round(scopeRect.height),
          left: Math.round(scopeRect.left),
          top: Math.round(scopeRect.top),
        } : {},
      });
    } catch (_) {}
    return exists;
  };
  const waitForChatEditor = async () => {
    for (let i = 0; i < 16; i += 1) {
      const editor = currentChatEditor();
      if (editor) return editor;
      await sleep(200);
    }
    return null;
  };
  const waitForSendButton = async editor => {
    for (let i = 0; i < 14; i += 1) {
      const send = clickableSendButton(editor);
      if (send) return send;
      await sleep(200);
    }
    return null;
  };
  const fillFollowup = async openingPayload => {
    const useContinuedFollowup = Boolean((openingPayload.alreadyInChat || openingPayload.continuedChat) && normalize(continuedFollowupMessage));
    const followupToSend = useContinuedFollowup ? continuedFollowupMessage : followupMessage;
    if (!normalize(followupToSend)) {
      return {
        ...openingPayload,
        followupFilled: false,
        followupSent: false,
        followupSkipped: true,
        followupMode: useContinuedFollowup ? 'continued' : 'initial',
        reason: '没有可发送的补充话术',
      };
    }
    const editor = await waitForChatEditor();
    if (!editor) {
      return {
        ...openingPayload,
        followupFilled: false,
        followupSent: false,
        reason: openingPayload.reason || '已开聊，但未找到聊天输入框',
      };
    }
    if (messageExistsInChat(followupToSend, editor)) {
      return {
        ...openingPayload,
        followupFilled: false,
        followupSent: false,
        followupAlreadyExists: true,
        followupMode: useContinuedFollowup ? 'continued' : 'initial',
        reason: '聊天记录中已存在本次补充话术，已跳过重复发送',
        editor: info(editor),
        sendButton: info(clickableSendButton(editor)),
        duplicateCheck: (() => {
          try { return JSON.parse(window.__liepin_last_followup_duplicate_check__ || '{}'); } catch (_) { return {}; }
        })(),
        followupLength: followupToSend.length,
      };
    }
    fillEditor(editor, followupToSend);
    await sleep(350);
    const send = await waitForSendButton(editor);
    if (dryRun) {
      return {
        ...openingPayload,
        followupFilled: true,
        followupSent: false,
        followupMode: useContinuedFollowup ? 'continued' : 'initial',
        reason: 'dry-run：已进入聊天并填入补充话术，未发送第二句',
        editor: info(editor),
        sendButton: info(send),
        followupLength: followupToSend.length,
      };
    }
    if (!send) {
      pressEnterToSend(editor);
      await sleep(700);
      const sentByEnter = isEditorClearedAfterSend(editor, followupToSend);
      return {
        ...openingPayload,
        followupFilled: true,
        followupSent: sentByEnter,
        followupMode: useContinuedFollowup ? 'continued' : 'initial',
        reason: sentByEnter
          ? '已填入补充话术，未找到发送按钮，已通过 Enter 发送'
          : '已填入补充话术，但未找到发送按钮，Enter 发送后输入框仍未清空',
        editor: info(editor),
        sendButton: {},
        sendFallback: 'enter',
        editorAfterSend: editorText(editor).slice(0, 120),
        sendCandidates: visibleSendCandidateInfo(editor),
        followupLength: followupToSend.length,
      };
    }
    clickLikeUser(send);
    await sleep(900);
    const sentByButton = isEditorClearedAfterSend(editor, followupToSend);
    if (!sentByButton) {
      pressEnterToSend(editor);
      await sleep(900);
      const sentAfterEnter = isEditorClearedAfterSend(editor, followupToSend);
      return {
        ...openingPayload,
        followupFilled: true,
        followupSent: sentAfterEnter,
        followupMode: useContinuedFollowup ? 'continued' : 'initial',
        reason: sentAfterEnter
          ? '点击发送后输入框未立即清空，已通过 Enter 补发成功'
          : '已点击发送并尝试 Enter，但输入框仍未清空，疑似未发送',
        editor: info(editor),
        sendButton: info(send),
        sendFallback: 'button_then_enter',
        editorAfterSend: editorText(editor).slice(0, 120),
        sendCandidates: visibleSendCandidateInfo(editor),
        followupLength: followupToSend.length,
      };
    }
    return {
      ...openingPayload,
      followupFilled: true,
      followupSent: sentByButton,
      followupMode: useContinuedFollowup ? 'continued' : 'initial',
      reason: sentByButton ? '已发送开聊语和补充话术' : '已点击发送按钮，但输入框未清空，疑似未发送',
      editor: info(editor),
      sendButton: info(send),
      editorAfterSend: editorText(editor).slice(0, 120),
      sendCandidates: visibleSendCandidateInfo(editor),
      followupLength: followupToSend.length,
    };
  };
  const existingEditor = currentChatEditor();
  if (existingEditor && /我的沟通|请输入文字|按Enter键发送/.test(document.body ? document.body.innerText || '' : '')) {
    return fillFollowup({
      opened: true,
      openingModalFound: false,
      openingSelected: false,
      openingSent: false,
      alreadyInChat: true,
      dryRun,
      openingMessage,
      followupMessage,
      continuedFollowupMessage,
    }).then(payload => JSON.stringify(payload));
  }
  const greet = controls().find(el => {
    const text = normalize(el.innerText || el.textContent || '');
    if (/查看联系方式|索要附件|索要简历|收藏|转发|超级聊聊|推荐职位/.test(text)) return false;
    return /立即沟通|继续沟通|打招呼/.test(text);
  });
  if (greet) {
    clickLikeUser(greet);
  }

  const runOpening = async () => {
    const chatEditorAfterClick = await waitForChatEditor();
    if (chatEditorAfterClick && /我的沟通|请输入文字|按Enter键发送|沟通职位|跳转沟通页/.test(document.body ? document.body.innerText || '' : '')) {
      return fillFollowup({
        opened: Boolean(greet),
        openingModalFound: false,
        openingSelected: false,
        openingSent: false,
        alreadyInChat: true,
        continuedChat: true,
        dryRun,
        greetButton: info(greet),
        openingMessage,
        followupMessage,
        continuedFollowupMessage,
      }).then(payload => JSON.stringify(payload));
    }
    const modalCandidates = [...document.querySelectorAll('.ant-modal, [role="dialog"], .ant-modal-content')]
      .filter(visible)
      .map(el => ({ el, text: normalize(el.innerText || el.textContent || '') }))
      .filter(item => /请选择职位开聊|打招呼语|不选择职位开聊/.test(item.text));
    const modal = modalCandidates[0] ? modalCandidates[0].el : null;
    if (!modal) {
      return JSON.stringify({
        opened: Boolean(greet),
        openingModalFound: false,
        openingSelected: false,
        openingSent: false,
        followupFilled: false,
        followupSent: false,
        dryRun,
        reason: '未找到职位开聊弹窗',
        greetButton: info(greet),
      });
    }
    const optionNodes = [...modal.querySelectorAll('label, .ant-radio-wrapper, [role="radio"], li, div')]
      .filter(visible)
      .map(el => ({ el, text: normalize(el.innerText || el.textContent || '') }))
      .filter(item => item.text.length >= 8 && item.text.length <= 180)
      .filter(item => /您好|你好|方便|职位|了解|回复|沟通|工作/.test(item.text));
    const seen = new Set();
    const greetingOptions = [];
    for (const item of optionNodes) {
      if (seen.has(item.text)) continue;
      seen.add(item.text);
      greetingOptions.push(item.text);
    }
    const targetOption = optionNodes.find(item => sameText(item.text, openingMessage) || item.text.includes(openingMessage) || openingMessage.includes(item.text));
    if (!targetOption) {
      return JSON.stringify({
        opened: Boolean(greet),
        openingModalFound: true,
        openingSelected: false,
        openingSent: false,
        followupFilled: false,
        followupSent: false,
        dryRun,
        reason: '未找到指定开聊招呼语',
        greetButton: info(greet),
        openingMessage,
        greetingOptions,
      });
    }
    clickLikeUser(targetOption.el);
    if (dryRun) {
      return JSON.stringify({
        opened: Boolean(greet),
        openingModalFound: true,
        openingSelected: true,
        openingSent: false,
        followupFilled: false,
        followupSent: false,
        dryRun,
        reason: 'dry-run：已选择指定开聊语，未点击“不选择职位开聊”',
        greetButton: info(greet),
        selectedGreeting: targetOption.text,
        openingMessage,
        greetingOptions,
      });
    }
    const noJobButton = [...modal.querySelectorAll('button, a, [role="button"], .ant-btn')]
      .filter(visible)
      .find(el => normalize(el.innerText || el.textContent || '') === '不选择职位开聊') || null;
    if (!noJobButton) {
      return JSON.stringify({
        opened: Boolean(greet),
        openingModalFound: true,
        openingSelected: true,
        openingSent: false,
        followupFilled: false,
        followupSent: false,
        dryRun,
        reason: '未找到“不选择职位开聊”按钮',
        greetButton: info(greet),
        selectedGreeting: targetOption.text,
        openingMessage,
        greetingOptions,
      });
    }
    clickLikeUser(noJobButton);
    return new Promise(resolve => setTimeout(() => {
      fillFollowup({
        opened: Boolean(greet),
        openingModalFound: true,
        openingSelected: true,
        openingSent: true,
        dryRun,
        greetButton: info(greet),
        selectedGreeting: targetOption.text,
        openingMessage,
        greetingOptions,
        noJobButton: info(noJobButton),
      }).then(payload => resolve(JSON.stringify(payload)));
    }, 1500));
  };
  return new Promise(resolve => setTimeout(() => {
    Promise.resolve(runOpening()).then(resolve);
  }, 900));
})();
"""
