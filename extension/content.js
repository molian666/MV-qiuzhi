/**
 * 抓取当前页面上的投递记录表格。
 *
 * 设计取舍：**不写死某家平台的 DOM 选择器**。
 * 招聘平台的页面结构改得非常勤，写死选择器的结果是"某天突然抓不到、而且没有任何提示"。
 * 所以这里用"通用表格识别"：先看表头像不像投递记录（至少要认出两个列名），
 * 再按表头取每一行的值。抓不到时返回明确的失败原因，让用户知道该去哪一页、或改用 CSV 导入。
 *
 * 注意：这个函数会被 chrome.scripting.executeScript 注入执行，因此**不能引用外部作用域**。
 */
function collectApplicationRows() {
  const HEADER_HINTS = ['公司', '企业', '职位', '岗位', '状态', '投递', '时间', '日期', '沟通', '反馈', '进展']

  function textOf(node) {
    return (node && node.textContent ? node.textContent : '').replace(/\s+/g, ' ').trim()
  }

  function scoreHeaders(headers) {
    return HEADER_HINTS.filter((hint) => headers.some((header) => header.includes(hint))).length
  }

  function rowsFromTable(table) {
    const headerCells = Array.from(
      table.querySelectorAll('thead tr:last-child th, thead tr:last-child td, tr:first-child th, tr:first-child td'),
    )
    const headers = headerCells.map(textOf)
    if (headers.length < 2 || scoreHeaders(headers) < 2) {
      return null
    }

    const bodyRows = Array.from(table.querySelectorAll('tbody tr'))
    const source = bodyRows.length > 0 ? bodyRows : Array.from(table.querySelectorAll('tr')).slice(1)

    const rows = source
      .map((tr) =>
        Object.fromEntries(
          Array.from(tr.children).map((cell, index) => [headers[index] || `列${index + 1}`, textOf(cell)]),
        ),
      )
      .filter((row) => Object.values(row).some((value) => value))
      .filter((row) => Object.keys(row).length >= 2)

    if (rows.length === 0) {
      return null
    }
    return { headers, rows, rowCount: rows.length, kind: 'table' }
  }

  // 1) 优先找真正的 <table>
  const tables = Array.from(document.querySelectorAll('table'))
  for (const table of tables) {
    const found = rowsFromTable(table)
    if (found) {
      return found
    }
  }

  // 2) 退而求其次：很多平台用 div 模拟表格（有 role="row"/"gridcell"）
  const gridRows = Array.from(document.querySelectorAll('[role="row"]'))
  if (gridRows.length > 1) {
    const headerCells = Array.from(gridRows[0].querySelectorAll('[role="columnheader"], td, th'))
    const headers = headerCells.map(textOf)
    if (headers.length >= 2 && scoreHeaders(headers) >= 2) {
      const rows = gridRows
        .slice(1)
        .map((row) =>
          Object.fromEntries(
            Array.from(row.querySelectorAll('[role="gridcell"], td')).map((cell, index) => [
              headers[index] || `列${index + 1}`,
              textOf(cell),
            ]),
          ),
        )
        .filter((row) => Object.values(row).some((value) => value))
      if (rows.length > 0) {
        return { headers, rows, rowCount: rows.length, kind: 'grid' }
      }
    }
  }

  return {
    headers: [],
    rows: [],
    rowCount: 0,
    kind: 'none',
    reason: '这一页没找到像"投递记录"的表格。请打开平台的「投递/沟通记录」页面后再同步，或用 CSV 导入。',
    pageTitle: document.title,
    url: location.href,
  }
}
