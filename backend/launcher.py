"""打包入口：PyInstaller 启动点。运行 uvicorn 托管 FastAPI 应用 + 内嵌前端。

密钥不打包：frozen 时 Settings 从 exe 旁 .env 读取（见 app/config.py），
本文件不含任何密钥。
"""
from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap() -> None:
    """onefile 模式下把解压目录放进 sys.path，确保 `app` 包可导入。"""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            sys.path.insert(0, str(Path(meipass)))


def main() -> None:
    _bootstrap()
    import logging
    import traceback

    import uvicorn

    from app.config import get_settings
    from app.main import app

    # GUI 模式（无控制台）下把启动日志写到 exe 旁的 logs/app.log，便于排障
    log_dir = Path(sys.executable).resolve().parent / "logs" if getattr(sys, "frozen", False) else Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)

    def _configure_logging() -> None:
        logging.basicConfig(
            level=logging.INFO,
            force=True,
            filename=log_dir / "app.log",
            encoding="utf-8",
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )

    try:
        _configure_logging()
        settings = get_settings()
        # log_config=None：跳过 uvicorn 的 dictConfig，让 uvicorn logger 传播到
        # 上面 basicConfig 挂到 root 的文件 handler，日志可靠落盘
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            log_level="info",
            log_config=None,
            access_log=False,
        )
    except Exception:
        traceback.print_exc()
        try:
            with open(log_dir / "error.log", "a", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except OSError:
            pass


if __name__ == "__main__":
    main()
