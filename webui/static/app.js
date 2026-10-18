'use strict';

/* ===================================================================
   纯函数区：不碰 DOM，可以用 node 直接单测
   =================================================================== */

/**
 * 从 SSE 缓冲区里切出完整的帧。
 * 返回 { frames: [{ev, data}], rest: 还没凑齐一帧的尾巴 }。
 *
 * SSE 一帧以空行结束。数据可能被 TCP 切成任意片段，
 * 所以必须把「没读全的尾巴」留给下一次调用。
 */
function parseSSEChunk(buffer) {
  const frames = [];
  let text = buffer;

  for (;;) {
    const boundary = text.indexOf('\n\n');
    if (boundary < 0) break;

    const raw = text.slice(0, boundary);
    text = text.slice(boundary + 2);

    let event = 'message';
    let data = '';

    for (const line of raw.split('\n')) {
      if (line.startsWith(':')) continue;                  // 保活注释帧，忽略
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) data += line.slice(5).trim();
    }

    if (!data) continue;

    try {
      frames.push({ ev: event, data: JSON.parse(data) });
    } catch (error) {
      // 坏帧就丢掉这一帧，不要因为一条消息毁掉整个流
    }
  }

  return { frames, rest: text };
}

const SPEECH_ERRORS = {
  'not-allowed': '语音识别被拒绝：浏览器没拿到麦克风权限，请点地址栏右侧的权限图标允许后重试。',
  'service-not-allowed': '浏览器拒绝了语音识别服务，请检查地址栏的权限设置。',
  'no-speech': '没有听到声音，请靠近麦克风再说一次。',
  'audio-capture': '找不到麦克风设备，请检查系统的输入设备。',
  'network': '语音识别服务连不上。Chrome 的语音识别依赖联网服务，离线或网络受限时不可用，请改用文字输入。',
  'aborted': '语音识别已中止。',
};

function speechErrorTextFor(code) {
  return SPEECH_ERRORS[code] || `语音识别出错：${code}`;
}

/* ===================================================================
   DOM 区：node 里不执行
   =================================================================== */

if (typeof document !== 'undefined') {
  (function () {
    const $ = (id) => document.getElementById(id);

    const els = {
      messages: $('messages'),
      emptyHint: $('empty-hint'),
      task: $('task'),
      mic: $('mic-btn'),
      send: $('send-btn'),
      stop: $('stop-btn'),
      speechWarn: $('speech-warn'),
      vaultWarn: $('vault-warn'),
      statusDot: $('status-dot'),
      modelName: $('model-name'),
      workspaceHint: $('workspace-hint'),
      credSelect: $('cred-select'),
      credManage: $('cred-manage'),
      credNote: $('cred-note'),
      vaultModal: $('vault-modal'),
      vaultClose: $('vault-close'),
      vaultProtector: $('vault-protector'),
      vaultPath: $('vault-path'),
      credList: $('cred-list'),
      credName: $('cred-name'),
      credKey: $('cred-key'),
      credSave: $('cred-save'),
    };

    let controller = null;    // AbortController
    let busy = false;
    let credentials = [];     // 只有名字和元信息，永远没有密钥

    /* ---------------- 提示与错误 ---------------- */

    function appendError(text) {
      const box = document.createElement('div');
      box.className = 'error';
      box.textContent = text;
      els.messages.append(box);
      scrollToBottom(box);
    }

    function showSpeechWarning(text) {
      els.speechWarn.hidden = false;
      els.speechWarn.textContent = text;
    }

    function hideSpeechWarning() {
      els.speechWarn.hidden = true;
    }

    function scrollToBottom(node) {
      (node || els.messages.lastElementChild)?.scrollIntoView({
        block: 'nearest',
        behavior: 'smooth',
      });
    }

    /* ---------------- 凭据柜 ----------------
       密钥不在这份代码能碰到的地方 —— 页面里从头到尾只有一个凭据**名字**。
       列表接口回的也是元信息，没有任何一条通路能把它读回来。
       ---------------------------------------- */

    function showVaultWarning(text) {
      els.vaultWarn.hidden = false;
      els.vaultWarn.textContent = text;
    }

    function hideVaultWarning() {
      els.vaultWarn.hidden = true;
    }

    /** 包一层 fetch：统一处理非 2xx 和后端的错误文案。 */
    async function callJson(url, options) {
      const response = await fetch(url, options);

      let data = null;
      try {
        data = await response.json();
      } catch (error) {
        data = null;
      }

      if (!response.ok) {
        throw new Error((data && data.error) || `服务器返回 ${response.status}`);
      }
      return data || {};
    }

    function selectedCredential() {
      return els.credSelect.value || '';
    }

    function updateCredNote() {
      const current = credentials.find((item) => item.name === els.credSelect.value);
      if (current && !current.readable_here) {
        els.credNote.textContent = '这条在本机解不开，删掉重新存一次';
        els.credNote.classList.add('missing');
        return;
      }
      els.credNote.textContent = '';
      els.credNote.classList.remove('missing');
    }

    function renderCredentialOptions() {
      const previous = els.credSelect.value;
      els.credSelect.innerHTML = '';

      if (credentials.length === 0) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = '（还没有凭据）';
        els.credSelect.append(option);
        els.credSelect.disabled = true;
        els.credNote.textContent = '点右边「管理…」存一把 API key';
        els.credNote.classList.add('missing');
        return;
      }

      els.credSelect.disabled = false;
      credentials.forEach((item) => {
        const option = document.createElement('option');
        option.value = item.name;
        // 换台机器就读不出来的那些，在下拉里也标出来
        option.textContent = item.readable_here ? item.name : `${item.name}（本机读不出）`;
        els.credSelect.append(option);
      });

      // 尽量保住用户原来选的那条，只有它没了才回落到第一条
      if (credentials.some((item) => item.name === previous)) {
        els.credSelect.value = previous;
      }
      updateCredNote();
    }

    function renderCredentialList() {
      els.credList.innerHTML = '';

      credentials.forEach((item) => {
        const row = document.createElement('div');
        row.className = 'cred-item' + (item.readable_here ? '' : ' unreadable');

        const name = document.createElement('span');
        name.className = 'cred-name';
        name.textContent = item.name;

        const meta = document.createElement('span');
        meta.className = 'cred-meta';
        meta.textContent = item.readable_here
          ? (item.created_at || '').replace('T', ' ')
          : '本机解不开';

        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'danger';
        remove.textContent = '删除';
        remove.addEventListener('click', () => deleteCredential(item.name));

        row.append(name, meta, remove);
        els.credList.append(row);
      });
    }

    async function loadCredentials() {
      let data;
      try {
        data = await callJson('/api/credentials');
      } catch (error) {
        els.credNote.textContent = `凭据柜读不出来：${error.message}`;
        els.credNote.classList.add('missing');
        return;
      }

      credentials = data.credentials || [];
      renderCredentialOptions();
      renderCredentialList();

      const protector = data.protector || {};
      els.vaultProtector.textContent = '保护方式：' + (protector.label || '未知');
      els.vaultPath.textContent = data.vault_path || '';

      // 没有系统加密就直说，不能让人以为背后有东西兜着
      if (protector.encrypted === false) {
        showVaultWarning(
          '本机没有可用的系统加密（Windows DPAPI / macOS 钥匙串），'
          + 'API key 是以明文存进 ' + (data.vault_path || '凭据文件')
          + ' 的（目录权限已收紧到只有你能读）。'
          + '跑完任务可以到「管理…」里删掉这条。'
        );
      } else {
        hideVaultWarning();
      }
    }

    async function saveCredential() {
      const name = els.credName.value.trim();
      const apiKey = els.credKey.value;

      if (!name) return appendError('请给这把 key 起个名字。');
      if (!apiKey.trim()) return appendError('请把 API key 粘进来。');

      els.credSave.disabled = true;
      try {
        const data = await callJson('/api/credentials', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name, api_key: apiKey }),
        });

        // 存完立刻清空：明文不该在 DOM 里多留一秒
        els.credName.value = '';
        els.credKey.value = '';

        await loadCredentials();
        if (data.credential && data.credential.name) {
          els.credSelect.value = data.credential.name;
        }
        els.credNote.classList.remove('missing');
        els.credNote.textContent = data.credential && data.credential.replaced
          ? '已覆盖同名凭据' : '已存入';
      } catch (error) {
        appendError(`存不进去：${error.message}`);
      } finally {
        els.credSave.disabled = false;
      }
    }

    async function deleteCredential(name) {
      if (!window.confirm(
        `删除凭据「${name}」？\n\n存进去的密钥是取不回来的，删掉就只能重新填一把。`
      )) {
        return;
      }

      try {
        await callJson('/api/credentials/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name }),
        });
      } catch (error) {
        return appendError(`删除失败：${error.message}`);
      }

      els.credKey.value = '';
      await loadCredentials();
    }

    function openVault() {
      els.vaultModal.hidden = false;
      loadCredentials();   // 打开就刷新一次，免得显示的是上次的旧状态
      els.credName.focus();
    }

    function closeVault() {
      els.vaultModal.hidden = true;
      // 关掉就把没提交的明文扫掉
      els.credKey.value = '';
    }

    els.credManage.addEventListener('click', openVault);
    els.vaultClose.addEventListener('click', closeVault);
    els.credSave.addEventListener('click', saveCredential);
    els.credSelect.addEventListener('change', updateCredNote);

    // 点遮罩关掉；点卡片里面不关
    els.vaultModal.addEventListener('click', (event) => {
      if (event.target === els.vaultModal) closeVault();
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !els.vaultModal.hidden) closeVault();
    });

    // 两个输入框里按回车就等于点「存入」
    [els.credName, els.credKey].forEach((input) => {
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          saveCredential();
        }
      });
    });

    /* ---------------- 语音 ---------------- */

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

    if (!SpeechRecognition) {
      // 不支持就明确告诉用户，不能静默失败
      els.mic.disabled = true;
      els.mic.title = '此浏览器不支持语音识别';
      showSpeechWarning(
        '此浏览器不支持语音识别（Web Speech API），请使用 Chrome 或 Edge。'
        + '其余功能不受影响，也可以直接输入文字。'
      );
    } else {
      const recognition = new SpeechRecognition();
      recognition.lang = 'zh-CN';
      recognition.interimResults = true;
      recognition.continuous = false;

      let listening = false;

      const setListening = (value) => {
        listening = value;
        els.mic.classList.toggle('recording', value);
        els.mic.textContent = value ? '⏹ 停止' : '🎤 语音';
      };

      recognition.onstart = () => setListening(true);
      recognition.onend = () => setListening(false);
      recognition.onerror = (event) => {
        setListening(false);
        showSpeechWarning(speechErrorTextFor(event.error));
      };
      recognition.onresult = (event) => {
        let text = '';
        for (let i = event.resultIndex; i < event.results.length; i += 1) {
          text += event.results[i][0].transcript;
        }
        els.task.value = (els.task.value + ' ' + text).trim();
      };

      els.mic.addEventListener('click', () => {
        if (listening) {
          recognition.stop();
          return;
        }
        hideSpeechWarning();
        try {
          recognition.start();
        } catch (error) {
          showSpeechWarning(`无法启动语音识别：${error.message}`);
        }
      });
    }

    /* ---------------- 渲染 ---------------- */

    function appendUserTurn(task) {
      els.emptyHint?.remove();

      const turn = document.createElement('div');
      turn.className = 'turn user';

      const bubble = document.createElement('div');
      bubble.className = 'bubble';
      bubble.textContent = task;
      turn.append(bubble);

      els.messages.append(turn);
      scrollToBottom(turn);
    }

    let currentStepBox = null;

    function stepBox(step) {
      if (currentStepBox && currentStepBox.dataset.step === String(step)) {
        return currentStepBox;
      }

      const box = document.createElement('div');
      box.className = 'step';
      box.dataset.step = String(step);

      const head = document.createElement('div');
      head.className = 'step-head';
      head.textContent = `步骤 ${step}`;
      box.append(head);

      els.messages.append(box);
      currentStepBox = box;
      return box;
    }

    function formatArguments(args) {
      if (!args || typeof args !== 'object') return '';
      return Object.keys(args)
        .map((key) => {
          const value = String(args[key]);
          return `${key}=${value.length > 80 ? value.slice(0, 80) + '…' : value}`;
        })
        .join(', ');
    }

    function renderEvent(eventName, data) {
      switch (eventName) {
        case 'step_start':
          stepBox(data.step);
          break;

        case 'assistant': {
          const box = stepBox(data.step);
          const calls = data.tool_calls || [];

          if (data.content) {
            const text = document.createElement('div');
            text.className = 'assistant-text';
            text.textContent = data.content;
            box.append(text);
          }
          calls.forEach((call) => {
            const line = document.createElement('div');
            line.className = 'tool-line';
            line.textContent = `→ 准备调用 ${call.tool}(${formatArguments(call.arguments)})`;
            box.append(line);
          });

          // 最后一步模型往往只给最终答案，这帧是空的，但 step_start 已经把
          // 卡片建出来了。这里把它撤掉，免得界面上留一个空框。
          if (!data.content && calls.length === 0
              && !box.querySelector('.assistant-text, .tool-line, .observation')) {
            box.remove();
            if (currentStepBox === box) currentStepBox = null;
          }
          break;
        }

        case 'tool_call':
          // 上一帧 assistant 已经把这个调用显示出来了，这里不重复渲染
          break;

        case 'observation': {
          const box = stepBox(data.step);
          const details = document.createElement('details');
          const blocked = data.blocked === true;
          details.className = 'observation '
            + (blocked ? 'blocked' : (data.success ? 'ok' : 'fail'));

          const summary = document.createElement('summary');
          // 「被拒绝」和「跑了但失败」是两回事，界面上要分得开
          summary.textContent = blocked
            ? `⊘ ${data.tool} 未执行（需要批准）`
            : `${data.success ? '✓' : '✗'} ${data.tool} 返回`;

          const pre = document.createElement('pre');
          pre.textContent = data.content;

          details.append(summary, pre);
          box.append(details);
          break;
        }

        case 'final': {
          const box = document.createElement('div');
          box.className = 'final';
          if (data.stopped) box.classList.add('stopped');
          box.textContent = data.content || '(没有内容)';
          els.messages.append(box);
          break;
        }

        case 'error':
          appendError(data.message + (data.detail ? '\n' + data.detail : ''));
          return;

        case 'cancelled':
          appendError('已停止。');
          return;

        case 'done':
          return;

        default:
          return;
      }

      scrollToBottom();
    }

    function setBusy(value) {
      busy = value;
      els.send.disabled = value;
      els.stop.hidden = !value;
      els.send.textContent = value ? '运行中…' : '发送';
    }

    /* ---------------- 提交 ---------------- */

    async function submit() {
      if (busy) return;

      const credential = selectedCredential();
      const task = els.task.value.trim();

      if (!credential) {
        return appendError('请先选一个凭据；没有的话点「管理…」新增一把。');
      }
      if (!task) {
        return appendError('请输入任务。');
      }

      // 请求体里只有凭据名 —— 密钥从来没到过浏览器，
      // 自然也不可能出现在这里、出现在浏览器历史里、或者被别的脚本读走。
      const body = JSON.stringify({
        credential: credential,
        task: task,
      });

      els.task.value = '';
      appendUserTurn(task);
      setBusy(true);

      controller = new AbortController();

      let response;
      try {
        response = await fetch('/api/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: body,
          signal: controller.signal,
        });
      } catch (error) {
        setBusy(false);
        if (error.name !== 'AbortError') appendError(`连不上服务器：${error.message}`);
        return;
      }

      if (!response.ok) {
        setBusy(false);
        return appendError(`服务器返回 ${response.status}：${await response.text()}`);
      }

      // 用 fetch + getReader 而不是 EventSource：EventSource 只能发 GET，
      // API key 就只好挂在 query string 上，那样会进浏览器历史和 Referer。
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const { frames, rest } = parseSSEChunk(buffer);
          buffer = rest;
          frames.forEach((frame) => renderEvent(frame.ev, frame.data));
        }
      } catch (error) {
        if (error.name !== 'AbortError') appendError(`流中断：${error.message}`);
      } finally {
        setBusy(false);
        controller = null;
      }
    }

    els.send.addEventListener('click', submit);

    els.stop.addEventListener('click', () => {
      if (controller) controller.abort();
    });

    els.task.addEventListener('keydown', (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
        event.preventDefault();
        submit();
      }
    });

    /* ---------------- 启动：探活 + 载入凭据 ---------------- */

    loadCredentials();

    fetch('/api/health')
      .then((response) => response.json())
      .then((health) => {
        els.statusDot.classList.add('ok');
        els.modelName.textContent = health.model || '';
        if (health.fake_model) {
          els.modelName.textContent = (health.model || '') + ' · 假模型模式';
          els.modelName.classList.add('fake');
        }
        if (health.workspace) {
          els.workspaceHint.textContent = '工作目录：' + health.workspace;
        }
      })
      .catch(() => {
        els.statusDot.classList.add('bad');
        els.modelName.textContent = '连不上服务';
      });
  })();
}

/* node 单测用 */
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { parseSSEChunk, speechErrorTextFor };
}
