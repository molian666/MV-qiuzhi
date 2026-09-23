/**
 * 扩展弹窗：抓当前页 → POST 到本地后端 /api/applications/ingest。
 *
 * 两个原则：
 * 1. 只读用户自己已登录的页面，不模拟登录、不走任何第三方服务器；
 * 2. 抓不到或同步失败时，**必须给出可执行的下一步**（去哪一页、改用 CSV），不能只报错。
 */

const DEFAULT_BACKEND = 'http://127.0.0.1:8000';

const PLATFORM_BY_HOST = [
  { match: 'zhipin.com', platform: 'boss' },
  { match: 'liepin.com', platform: 'liepin' },
  { match: 'zhaopin.com', platform: 'zhilian' },
  { match: '51job.com', platform: '51job' },
  { match: 'lagou.com', platform: 'lagou' },
];

const resultBox = document.getElementById('result');
const syncButton = document.getElementById('sync');
const optionsButton = document.getElementById('openOptions');

function show(message, kind = '') {
  resultBox.className = kind;
  resultBox.textContent = message;
}

async function backendUrl() {
  const stored = await chrome.storage.local.get('backend');
  return (stored.backend || DEFAULT_BACKEND).replace(/\/$/, '');
}

function platformOf(url) {
  const found = PLATFORM_BY_HOST.find((item) => url.includes(item.match));
  return found ? found.platform : '';
}

async function sync() {
  syncButton.disabled = true;
  show('正在读取当前页面…');

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id || !tab.url) {
      show('拿不到当前标签页，请在招聘网站上打开投递记录页后重试。', 'err');
      return;
    }
    if (!tab.url.startsWith('http')) {
      show('当前不是普通网页（可能是扩展页或本地文件），请切到招聘网站页面。', 'err');
      return;
    }

    const [injection] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: collectApplicationRows,
    });
    const collected = injection?.result;
    if (!collected || collected.rowCount === 0) {
      show(collected?.reason || '这一页没有抓到投递记录，请打开「投递记录」页面后再同步。', 'warn');
      return;
    }

    show(`抓到 ${collected.rowCount} 行（表头：${collected.headers.slice(0, 5).join(' / ')}），正在同步…`);

    const response = await fetch(`${await backendUrl()}/api/applications/ingest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ platform: platformOf(tab.url), rows: collected.rows }),
    });

    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      show(`同步失败：${body.detail || response.status}\n\n可以把这一页导出成 CSV，用「投递分析 → 导入」贴进来。`, 'err');
      return;
    }

    const lines = [`新增 ${body.inserted} 条，更新 ${body.updated} 条，跳过（已存在）${body.skipped} 条`];
    if (body.unmatched_headers && body.unmatched_headers.length > 0) {
      lines.push(`没认出来的列：${body.unmatched_headers.join(' / ')}（这些列的数据没有被导入）`);
    }
    if (body.note) {
      lines.push(body.note);
    }
    show(lines.join('\n'), 'ok');
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    show(
      `同步失败：${message}\n\n` +
        '常见原因：本地后端没启动（在项目目录跑 scripts/dev.ps1），或后端地址不对（点下面的设置）。',
      'err',
    );
  } finally {
    syncButton.disabled = false;
  }
}

syncButton.addEventListener('click', sync);

optionsButton.addEventListener('click', async () => {
  const current = await backendUrl();
  const next = window.prompt('后端地址（默认 http://127.0.0.1:8000）', current);
  if (next) {
    await chrome.storage.local.set({ backend: next.trim() });
    show(`后端地址已保存：${next.trim()}`, 'ok');
  }
});

// 说明当前看到的平台，减少"为什么同步到别的渠道了"的困惑
chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
  const platform = tab?.url ? platformOf(tab.url) : '';
  if (platform) {
    show(`当前页面识别为：${platform}。点上面的按钮开始同步。`);
  } else {
    show('当前页面不在已知招聘平台列表里，仍可尝试同步（渠道会记成 other）。');
  }
});
