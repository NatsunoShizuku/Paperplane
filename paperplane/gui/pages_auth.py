"""页面 4：Telegram 账号与分步登录。"""
from __future__ import annotations

from pathlib import Path

from ..config import ConfigError, load_config
from ..service import AuthSession


def build_auth_page(
    ui,
    *,
    cfg_path: Path,
    auth_holder: dict[str, AuthSession | None],
):
    """构建账号与登录页面，返回 page_container。"""
    with ui.column().classes("w-full items-center") as page_account:
        page_account.set_visibility(False)

        with ui.card().classes("w-full max-w-xl m3-card p-6 gap-3"):
            with ui.row().classes("items-center gap-3"):
                with ui.avatar(color="indigo-6", text_color="white", size="44px"):
                    ui.icon("verified_user", size="22px")
                with ui.column().classes("gap-0"):
                    ui.label("Telegram 账号登录").classes("text-lg font-semibold m3-text")
                    ui.label("Session 仅保存在本地 data/ 目录，支持 macOS 与 Windows 共用").classes("text-xs m3-muted")

            login_chip = ui.chip("尚未检测登录状态", icon="help_outline", color="grey-7", text_color="white").props("dense")

            in_phone = ui.input("手机号（带国际区号，如 +8613800138000）").props("outlined dense").classes("w-full")
            in_code = ui.input("验证码（查收 Telegram 官方消息或短信）").props("outlined dense").classes("w-full")
            in_pwd = ui.input("两步验证密码（若开启 2FA 再填写）", password=True, password_toggle_button=True).props("outlined dense").classes("w-full")

            async def check_login():
                try:
                    cfg = load_config(cfg_path)
                    sess = AuthSession(cfg)
                    me = await sess.get_me()
                    await sess.disconnect()
                    if me:
                        login_chip.set_text(f"已登录：{me.first_name}（id={me.id}）")
                        login_chip.props("color=positive text-color=white icon=check_circle")
                    else:
                        login_chip.set_text("未登录，请填写手机号并获取验证码")
                        login_chip.props("color=warning text-color=white icon=warning")
                except (ConfigError, Exception) as e:
                    login_chip.set_text(f"检测失败：{e}")
                    login_chip.props("color=negative text-color=white icon=error")

            async def send_code_click():
                try:
                    cfg = load_config(cfg_path)
                    if auth_holder["session"]:
                        await auth_holder["session"].disconnect()
                    sess = AuthSession(cfg)
                    await sess.send_code(in_phone.value)
                    auth_holder["session"] = sess
                    login_chip.set_text("验证码已发送，请在下方输入并确认登录")
                    login_chip.props("color=info text-color=white icon=mark_email_read")
                    ui.notify("验证码已发送", type="positive")
                except (ConfigError, Exception) as e:
                    ui.notify(f"发送失败：{e}", type="negative")

            async def sign_in_click():
                sess = auth_holder.get("session")
                if not sess:
                    ui.notify("请先点击「发送验证码」", type="warning")
                    return
                try:
                    me = await sess.sign_in(in_code.value, in_pwd.value)
                    await sess.disconnect()
                    auth_holder["session"] = None
                    login_chip.set_text(f"登录成功：{me.first_name}（id={me.id}）")
                    login_chip.props("color=positive text-color=white icon=check_circle")
                    ui.notify("登录成功，session 已保存", type="positive")
                except Exception as e:
                    ui.notify(f"登录失败：{type(e).__name__}: {e}", type="negative")

            with ui.row().classes("gap-3 mt-2 flex-wrap"):
                ui.button("检测状态", icon="refresh", on_click=check_login).props("outline color=primary").classes("m3-pill")
                ui.button("发送验证码", icon="sms", color="secondary", on_click=send_code_click).props("unelevated").classes("m3-pill")
                ui.button("确认登录", icon="login", color="primary", on_click=sign_in_click).props("unelevated").classes("m3-pill px-5")

    return page_account
