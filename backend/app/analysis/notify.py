"""通知内容组装（纯函数）。

纯函数放在 `analysis/` 是项目的硬约定（不碰 DB、不碰网络），所以"发出去的消息长什么样"
能直接单测，而不用起服务或真的往机器人发一条。

**为什么通知文案要单独设计**：
- 手机上看的是**一行摘要**，所以标题必须自带关键信息（哪家公司、什么状态），不能写成"投递通知"；
- 熔断与验证码是**要用户立刻行动**的（去手动过验证 / 今天别再投），所以这类消息必须带"下一步做什么"；
- 通知是**附带能力**，文本不该长得像报告——长消息在机器人里会被截断。

**不编造细节**：没有的信息就不写。岗位薪资/城市为空时整段省略，而不是填"未知"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

#: 会触发通知的事件。加新事件时这里与文案一起改，`test` 专供"测试连通性"按钮使用。
NotifyKind = Literal["sent", "circuit", "captcha", "failed", "test"]

KIND_LABELS: dict[str, str] = {
    "sent": "投递成功",
    "circuit": "已熔断停投",
    "captcha": "需要你手动处理验证码",
    "failed": "投递失败",
    "test": "通知测试",
}


@dataclass(slots=True)
class Notification:
    """一条待发送的通知。`summary` 是一行摘要，`lines` 是正文要点。"""

    kind: str
    title: str
    summary: str
    lines: list[str] = field(default_factory=list)

    @property
    def body(self) -> str:
        """正文（企业微信机器人 text 类型的 `content` 就是它）。"""
        return "\n".join([self.summary, *self.lines]) if self.lines else self.summary


def _job_line(company: str, title: str) -> str:
    parts = [part.strip() for part in (company, title) if part and part.strip()]
    return " · ".join(parts) if parts else "（岗位信息缺失）"


def build_notification(kind: str, **payload: Any) -> Notification:
    """按事件类型组装通知。

    未知 `kind` 会返回一条兜底通知而不是抛错——通知是附带能力，
    **它自己不该成为新的失败点**（抛错会把整轮投递打断）。
    """
    company = str(payload.get("company") or "")
    job_title = str(payload.get("title") or "")
    job = _job_line(company, job_title)

    if kind == "sent":
        lines: list[str] = []
        detail = str(payload.get("detail") or "").strip()
        if detail:
            lines.append(detail)
        sent_today = payload.get("sent_today")
        daily_limit = payload.get("daily_limit")
        if sent_today is not None and daily_limit is not None:
            lines.append(f"今日已投 {sent_today}/{daily_limit}")
        return Notification(kind=kind, title=KIND_LABELS["sent"], summary=f"已投递：{job}", lines=lines)

    if kind == "circuit":
        # 这条最重要：用户必须知道"今天别再投了"，否则他可能手动去投、把账号搞得更糟
        lines = [str(payload.get("detail") or "检测到平台风控信号。").strip()]
        lines.append("**今天不要再投递**。先到平台上确认账号状态，明天再继续。")
        until = payload.get("until")
        if until:
            lines.append(f"熔断恢复时间：{until}")
        return Notification(
            kind=kind,
            title=KIND_LABELS["circuit"],
            summary="已停止今天的投递（检测到风控/验证码）",
            lines=[line for line in lines if line],
        )

    if kind == "captcha":
        lines = [str(payload.get("detail") or "出现了验证码或滑块。").strip()]
        lines.append("请到浏览器里手动完成验证，完成后程序会从当前这条继续。")
        return Notification(
            kind=kind,
            title=KIND_LABELS["captcha"],
            summary="投递已暂停，等你手动过验证码",
            lines=[line for line in lines if line],
        )

    if kind == "failed":
        lines = [str(payload.get("detail") or "未给出原因").strip()]
        attempts = payload.get("attempts")
        if attempts is not None:
            lines.append(f"已尝试 {attempts} 次")
        return Notification(
            kind=kind,
            title=KIND_LABELS["failed"],
            summary=f"投递失败：{job}",
            lines=[line for line in lines if line],
        )

    if kind == "test":
        return Notification(
            kind=kind,
            title=KIND_LABELS["test"],
            summary="通知通道已接通",
            lines=["这条消息由「投递执行台 → 通知」的测试按钮发出。收到就说明配置没问题。"],
        )

    return Notification(
        kind=str(kind),
        title="投递助手",
        summary=str(payload.get("summary") or f"事件：{kind}"),
    )


def webhook_payload(notification: Notification) -> dict[str, Any]:
    """企业微信机器人（`qyapi.weixin.qq.com/cgi-bin/webhook/send`）的请求体。

    **只发 text，不发 markdown**：markdown 在企业微信里对缩进/换行敏感，
    而且在手机上反而更难读；纯文本两种情况都能看。
    `mentioned_list` 留空——默认 @所有人 会被群成员讨厌，而且这条通知本来只给用户自己看。
    """
    return {"msgtype": "text", "text": {"content": f"【{notification.title}】{notification.body}"}}
